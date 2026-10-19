#!/usr/bin/env python3
"""Wayland-friendly local voice-to-text daemon for Linux."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import platform
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

APP_DIR = Path.home() / ".local" / "share" / "voice-to-text"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/voice-to-text-{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / "voice-to-text.sock"
STATE_PATH = RUNTIME_DIR / "voice-to-text.state"  # read by the GNOME Shell overlay
LOG_PATH = APP_DIR / "voice-to-text.log"
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SECONDS = 0.05
LEVEL_LOG_SECONDS = 1.0  # how often to log a mic level summary while recording
MODEL_NAME = os.environ.get("VTT_MODEL", "small")
LANGUAGE = os.environ.get("VTT_LANGUAGE", "auto")  # auto, en, bn
COMPUTE_TYPE = os.environ.get("VTT_COMPUTE_TYPE", "int8")
LOG_LEVEL = os.environ.get("VTT_LOG_LEVEL", "DEBUG").upper()

log = logging.getLogger("voice-to-text")


def setup_logging(role: str) -> None:
    """Log to a rotating file shared by daemon and clients, plus stderr for interactive/systemd runs."""
    level = getattr(logging, LOG_LEVEL, logging.DEBUG)
    fmt = logging.Formatter(
        f"%(asctime)s.%(msecs)03d %(levelname)-7s [{role} pid=%(process)d %(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers: list[logging.Handler] = []
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        ))
    except OSError as exc:
        print(f"Could not open log file {LOG_PATH}: {exc}", file=sys.stderr)
    # An auto-spawned daemon's stderr is daemon.log; skip it there to avoid duplicating every line.
    if sys.stderr.isatty() or os.environ.get("JOURNAL_STREAM") or not handlers:
        handlers.append(logging.StreamHandler(sys.stderr))
    root = logging.getLogger()
    root.setLevel(logging.WARNING)  # keep third-party chatter (urllib3, filelock, …) quiet
    for handler in handlers:
        handler.setFormatter(fmt)
        root.addHandler(handler)
    for name in ("voice-to-text", "faster_whisper"):
        logging.getLogger(name).setLevel(level)
    sys.excepthook = lambda *exc_info: log.critical("Uncaught exception", exc_info=exc_info)
    threading.excepthook = lambda args: log.critical(
        "Uncaught exception in thread %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def pick_input_device() -> int | str | None:
    """Raw ALSA hw devices reject 16 kHz, so prefer the PulseAudio/PipeWire default source."""
    # PortAudio snapshots devices and its PulseAudio connection at init; re-init so a PipeWire
    # restart or a hot-plugged mic doesn't leave the long-running daemon with a dead stream.
    start = time.perf_counter()
    sd._terminate()
    sd._initialize()
    log.debug("Re-initialized PortAudio in %.3fs", time.perf_counter() - start)
    override = os.environ.get("VTT_DEVICE")
    if override:
        log.info("Using input device from VTT_DEVICE: %r", override)
        return int(override) if override.isdigit() else override
    devices = sd.query_devices()
    for index, dev in enumerate(devices):
        log.debug(
            "Audio device %d: %r in=%d out=%d default_sr=%.0f",
            index, dev["name"], dev["max_input_channels"], dev["max_output_channels"], dev["default_samplerate"],
        )
    for index, dev in enumerate(devices):
        if dev["max_input_channels"] > 0 and dev["name"] in ("pulse", "Default Source", "pipewire", "default"):
            log.info("Selected input device %d: %r", index, dev["name"])
            return index
    log.warning("No pulse/pipewire input device found; falling back to the PortAudio default")
    return None


def notify(message: str, *, popup: bool = False) -> None:
    """Log a status message; only show a desktop popup for problems the overlay can't show."""
    log.info("%s", message)
    if not popup:
        return
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=Voice to Text", "Voice to Text", message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.warning("notify-send not found; popup not shown")


def run_cmd(args: list[str], *, input_text: str | None = None, timeout: float = 5) -> subprocess.CompletedProcess:
    log.debug("Running %s", args)
    start = time.perf_counter()
    result = subprocess.run(
        args, input=input_text, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    log.debug(
        "%s exited %d in %.3fs; stdout=%r stderr=%r",
        args[0], result.returncode, time.perf_counter() - start, result.stdout.strip(), result.stderr.strip(),
    )
    return result


class VoiceToTextDaemon:
    def __init__(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.model: WhisperModel | None = None
        self.lock = threading.Lock()
        self.recording = False
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.server: socket.socket | None = None
        self.cancelled = False
        self.last_state: tuple[str, str] | None = None
        self.write_state("idle")

    def write_state(self, state: str, level: float = 0.0, message: str = "") -> None:
        """Publish state for the on-screen overlay (atomic replace so readers never see partial JSON).

        States: idle, recording, processing (message = current step), error/warning (message shown briefly).
        """
        if (state, message) != self.last_state:
            log.debug("State %s -> %s %r", self.last_state, state, message)
            self.last_state = (state, message)
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(
                {"state": state, "level": round(level, 3), "message": message, "ts": time.time()}
            ))
            os.replace(tmp, STATE_PATH)
        except OSError as exc:
            log.error("Could not write state file %s: %s", STATE_PATH, exc)

    def cancel(self) -> str:
        with self.lock:
            if not self.recording:
                log.info("Cancel requested but not recording")
                return "Nothing to cancel."
            log.info("Cancel requested; discarding current recording")
            self.cancelled = True
            self.stop_event.set()
            return "Cancelled."

    def load_model(self) -> WhisperModel:
        if self.model is None:
            self.write_state("processing", message=f"Loading {MODEL_NAME} model…")
            notify(f"Loading local Whisper model: {MODEL_NAME} (device=cpu, compute_type={COMPUTE_TYPE})")
            start = time.perf_counter()
            self.model = WhisperModel(MODEL_NAME, device="cpu", compute_type=COMPUTE_TYPE)
            log.info("Model %s loaded in %.2fs", MODEL_NAME, time.perf_counter() - start)
        else:
            log.debug("Using cached model %s", MODEL_NAME)
        return self.model

    def toggle(self) -> str:
        with self.lock:
            if self.recording:
                log.info("Toggle: stopping recording")
                self.stop_event.set()
                return "Stopping recording…"
            log.info("Toggle: starting recording")
            self.recording = True
            self.cancelled = False
            self.stop_event = threading.Event()
            self.worker = threading.Thread(target=self.record_and_transcribe, name="recorder", daemon=True)
            self.worker.start()
            return "Recording started. Press the shortcut again to finish."

    def record_and_transcribe(self) -> None:
        notice: tuple[str, str] | None = None  # (error|warning, message) shown by the overlay when done
        try:
            # notify("Recording… speak now. Press the shortcut to finish.")
            chunks: list[np.ndarray] = []
            blocksize = int(SAMPLE_RATE * BLOCK_SECONDS)
            device = pick_input_device()
            overflows = 0
            window_peak = 0.0
            window_rms: list[float] = []
            next_level_log = time.monotonic() + LEVEL_LOG_SECONDS

            log.debug("Opening input stream: device=%r rate=%d channels=%d blocksize=%d",
                      device, SAMPLE_RATE, CHANNELS, blocksize)
            open_start = time.perf_counter()
            with sd.InputStream(
                device=device,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=blocksize,
            ) as stream:
                log.info("Recording started (stream opened in %.3fs, latency %.3fs)",
                         time.perf_counter() - open_start, stream.latency)
                while not self.stop_event.is_set():
                    data, overflowed = stream.read(blocksize)
                    if overflowed:
                        overflows += 1
                        log.warning("Microphone input overflow (#%d)", overflows)
                    mono = np.asarray(data, dtype=np.float32).reshape(-1)
                    chunks.append(mono.copy())
                    rms = float(np.sqrt(np.mean(np.square(mono)) + 1e-12))
                    window_rms.append(rms)
                    window_peak = max(window_peak, float(np.max(np.abs(mono))))
                    self.write_state("recording", min(1.0, (rms ** 0.5) * 2.5))
                    if time.monotonic() >= next_level_log:
                        log.debug("Recording %.1fs: rms avg=%.4f max=%.4f peak=%.4f",
                                  len(chunks) * BLOCK_SECONDS, sum(window_rms) / len(window_rms),
                                  max(window_rms), window_peak)
                        window_peak, window_rms = 0.0, []
                        next_level_log += LEVEL_LOG_SECONDS

            log.info("Recording stopped: %d blocks (%.2fs), %d overflow(s)",
                     len(chunks), len(chunks) * BLOCK_SECONDS, overflows)

            if self.cancelled:
                # notify("Recording cancelled.")
                log.info("Recording cancelled; discarding audio")
                return

            if not chunks:
                # notify("No audio recorded.")
                log.warning("No audio recorded")
                notice = ("warning", "No audio recorded")
                return

            audio = np.concatenate(chunks)
            duration = len(audio) / SAMPLE_RATE
            peak = float(np.max(np.abs(audio)))
            log.info("Audio: %.2fs, %d samples, peak=%.4f, rms=%.4f",
                     duration, len(audio), peak, float(np.sqrt(np.mean(np.square(audio)))))
            silent = peak < 1e-3
            if silent:
                log.warning("Audio is near-silent (peak %.5f); is the right microphone selected / unmuted?", peak)
            if len(audio) < int(0.25 * SAMPLE_RATE):
                # notify("Recording was too short.")
                log.warning("Recording too short (%.2fs < 0.25s); skipping", duration)
                notice = ("warning", "Recording too short")
                return

            wav_path = APP_DIR / f"recording-{int(time.time())}.wav"
            import wave
            pcm = np.clip(audio, -1.0, 1.0)
            pcm16 = (pcm * 32767).astype(np.int16)
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm16.tobytes())
            log.info("Saved %s (%d bytes)", wav_path, wav_path.stat().st_size)

            self.write_state("processing", message="Preparing…")
            # notify("Transcribing locally…")
            processing_start = time.perf_counter()
            model = self.load_model()
            load_seconds = time.perf_counter() - processing_start
            transcribe_start = time.perf_counter()
            lang = None if LANGUAGE.lower() == "auto" else LANGUAGE.lower()
            self.write_state("processing", message="Transcribing…")
            log.info("Transcribing: model=%s language=%s beam_size=5 vad_filter=True", MODEL_NAME, lang or "auto")
            segments, info = model.transcribe(
                audio,  # pass the array; avoids PyAV file decoding
                language=lang,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            log.info("Detected language %s (p=%.2f); duration %.2fs, after VAD %.2fs",
                     info.language, info.language_probability, info.duration,
                     getattr(info, "duration_after_vad", float("nan")))
            parts: list[str] = []
            for seg in segments:  # generator: decoding happens while iterating
                log.debug("Segment %d [%.2f-%.2f] avg_logprob=%.3f no_speech=%.3f compression=%.2f: %r",
                          seg.id, seg.start, seg.end, seg.avg_logprob, seg.no_speech_prob,
                          seg.compression_ratio, seg.text)
                parts.append(seg.text)
                if info.duration > 0:
                    pct = min(99, int(seg.end / info.duration * 100))
                    self.write_state("processing", message=f"Transcribing… {pct}%")
            text = "".join(parts).strip()
            transcribe_seconds = time.perf_counter() - transcribe_start
            log.info("Transcribed %d segment(s) in %.2fs", len(parts), transcribe_seconds)
            if not text:
                # notify("No speech recognized.")
                log.warning("No speech recognized")
                notice = ("warning", "No sound from microphone" if silent else "No speech recognized")
                return

            # Put recognized text on the Wayland clipboard.
            # wl-copy stays alive to serve the clipboard, so don't capture its pipes.
            self.write_state("processing", message="Pasting…")
            log.debug("Copying %d chars to clipboard with wl-copy", len(text))
            copier = subprocess.Popen(
                ["wl-copy", "--type", "text/plain;charset=utf-8"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            copier.stdin.write(text.encode("utf-8"))
            copier.stdin.close()
            time.sleep(0.1)
            if copier.poll() not in (None, 0):
                raise RuntimeError(f"wl-copy failed with exit code {copier.returncode}")
            log.info("Copied to clipboard (wl-copy pid=%d)", copier.pid)

            # Give the clipboard manager a moment, then paste into the focused app.
            time.sleep(0.25)
            paste = run_cmd(["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"], timeout=5)
            if paste.returncode != 0:
                # notify("Text copied, but auto-paste failed. Paste with Ctrl+V.", popup=True)
                log.error("Auto-paste failed (wtype exit %d): %s", paste.returncode, paste.stderr.strip())
                notice = ("warning", "Copied — auto-paste failed, press Ctrl+V")
            else:
                log.info("Pasted into focused app via wtype")
            total_seconds = time.perf_counter() - processing_start
            log.info("Transcription (%s): %s", info.language, text)
            log.info("Timing: audio %.1fs | model load %.2fs | transcribe %.2fs | total after recording %.2fs",
                     duration, load_seconds, transcribe_seconds, total_seconds)
        except sd.PortAudioError as exc:
            log.exception("Microphone error")
            notice = ("error", f"Microphone error: {exc}")
        except Exception as exc:
            # notify(f"Error: {exc}", popup=True)
            log.exception("Recording/transcription failed")
            notice = ("error", f"{type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.recording = False
                self.stop_event.clear()
            if notice:
                self.write_state(notice[0], message=notice[1])
            else:
                self.write_state("idle")

    def serve(self) -> None:
        log.info("Daemon starting: python %s on %s, pid %d", platform.python_version(), platform.platform(), os.getpid())
        log.info("Config: model=%s language=%s compute_type=%s device=%s log_level=%s",
                 MODEL_NAME, LANGUAGE, COMPUTE_TYPE, os.environ.get("VTT_DEVICE", "auto"), LOG_LEVEL)
        log.info("Paths: socket=%s state=%s app_dir=%s log=%s", SOCKET_PATH, STATE_PATH, APP_DIR, LOG_PATH)
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            SOCKET_PATH.unlink()
            log.debug("Removed stale socket %s", SOCKET_PATH)
        except FileNotFoundError:
            pass
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o600)
        self.server.listen(8)
        log.info("Voice-to-Text daemon listening on %s", SOCKET_PATH)
        while True:
            conn, _ = self.server.accept()
            with conn:
                try:
                    command = conn.recv(128).decode("utf-8", "replace").strip()
                    if command == "toggle":
                        result = self.toggle()
                    elif command == "cancel":
                        result = self.cancel()
                    elif command == "status":
                        result = "recording" if self.recording else "idle"
                    else:
                        log.warning("Unknown command %r", command)
                        result = "unknown command"
                    # status is polled frequently (e.g. by clients waiting for startup), so keep it at DEBUG
                    log.log(logging.DEBUG if command == "status" else logging.INFO,
                            "Command %r -> %r", command, result)
                    conn.sendall(result.encode())
                except Exception as exc:
                    log.exception("Error handling client command")
                    try:
                        conn.sendall(f"error: {exc}".encode())
                    except OSError:
                        pass  # client (e.g. the overlay) already hung up


def send_command(command: str) -> str:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(command.encode())
        return client.recv(1024).decode("utf-8", "replace")
    finally:
        client.close()


def start_daemon(timeout: float = 30) -> None:
    """Spawn a detached daemon and wait until its socket accepts connections."""
    lock_path = RUNTIME_DIR / "voice-to-text.lock"
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        log.debug("Waiting for startup lock %s", lock_path)
        fcntl.flock(lock, fcntl.LOCK_EX)  # a second key press waits here instead of spawning another daemon
        try:
            send_command("status")
            log.info("Daemon was started by another client while waiting for the lock")
            return  # someone else started it while we waited for the lock
        except OSError:
            pass
        APP_DIR.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        with open(APP_DIR / "daemon.log", "ab") as log_file:
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "daemon"],
                stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, start_new_session=True,
            )
        log.info("Spawned daemon pid %d (%s)", proc.pid, sys.executable)
        deadline = start + timeout
        while time.monotonic() < deadline:
            try:
                send_command("status")
                log.info("Daemon ready after %.2fs", time.monotonic() - start)
                return
            except OSError:
                if proc.poll() is not None:
                    log.error("Daemon exited on startup with code %d", proc.returncode)
                    raise OSError(f"daemon exited on startup (see {APP_DIR / 'daemon.log'})") from None
                time.sleep(0.1)
        log.error("Daemon did not start within %.0fs", timeout)
        raise TimeoutError(f"daemon did not start within {timeout:.0f}s (see {APP_DIR / 'daemon.log'})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Wayland voice-to-text")
    parser.add_argument("command", choices=["daemon", "toggle", "cancel", "status"])
    args = parser.parse_args()
    setup_logging(args.command)
    if args.command == "daemon":
        try:
            VoiceToTextDaemon().serve()
        except KeyboardInterrupt:
            log.info("Daemon stopped by keyboard interrupt")
        return 0
    try:
        try:
            response = send_command(args.command)
        except OSError as exc:
            if args.command != "toggle":
                raise
            log.info("Daemon not reachable (%s); starting it", exc)
            start_daemon()
            response = send_command(args.command)
        log.log(logging.DEBUG if args.command == "status" else logging.INFO,
                "Sent %r -> %r", args.command, response)
        print(response)
        return 0
    except OSError as exc:
        log.error("Voice-to-Text daemon is not running: %s", exc)
        print("Start it with: systemctl --user start voice-to-text.service", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
