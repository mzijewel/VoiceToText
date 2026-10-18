#!/usr/bin/env python3
"""Wayland-friendly local voice-to-text daemon for Linux."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
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
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SECONDS = 0.05
MODEL_NAME = os.environ.get("VTT_MODEL", "small")
LANGUAGE = os.environ.get("VTT_LANGUAGE", "auto")  # auto, en, bn
COMPUTE_TYPE = os.environ.get("VTT_COMPUTE_TYPE", "int8")


def pick_input_device() -> int | str | None:
    """Raw ALSA hw devices reject 16 kHz, so prefer the PulseAudio/PipeWire default source."""
    override = os.environ.get("VTT_DEVICE")
    if override:
        return int(override) if override.isdigit() else override
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and dev["name"] in ("pulse", "Default Source", "pipewire", "default"):
            return index
    return None


def notify(message: str, *, popup: bool = False) -> None:
    """Log a status message; only show a desktop popup for problems the overlay can't show."""
    print(message, flush=True)
    if not popup:
        return
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=Voice to Text", "Voice to Text", message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def run_cmd(args: list[str], *, input_text: str | None = None, timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, input=input_text, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=False,
    )


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
        self.write_state("idle")

    def write_state(self, state: str, level: float = 0.0) -> None:
        """Publish state for the on-screen overlay (atomic replace so readers never see partial JSON)."""
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"state": state, "level": round(level, 3), "ts": time.time()}))
            os.replace(tmp, STATE_PATH)
        except OSError as exc:
            print(f"Could not write state file: {exc}", file=sys.stderr)

    def cancel(self) -> str:
        with self.lock:
            if not self.recording:
                return "Nothing to cancel."
            self.cancelled = True
            self.stop_event.set()
            return "Cancelled."

    def load_model(self) -> WhisperModel:
        if self.model is None:
            notify(f"Loading local Whisper model: {MODEL_NAME}")
            self.model = WhisperModel(MODEL_NAME, device="cpu", compute_type=COMPUTE_TYPE)
        return self.model

    def toggle(self) -> str:
        with self.lock:
            if self.recording:
                self.stop_event.set()
                return "Stopping recording…"
            self.recording = True
            self.cancelled = False
            self.stop_event = threading.Event()
            self.worker = threading.Thread(target=self.record_and_transcribe, daemon=True)
            self.worker.start()
            return "Recording started. Press the shortcut again to finish."

    def record_and_transcribe(self) -> None:
        try:
            # notify("Recording… speak now. Press the shortcut to finish.")
            chunks: list[np.ndarray] = []
            blocksize = int(SAMPLE_RATE * BLOCK_SECONDS)

            with sd.InputStream(
                device=pick_input_device(),
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=blocksize,
            ) as stream:
                while not self.stop_event.is_set():
                    data, overflowed = stream.read(blocksize)
                    if overflowed:
                        print("Warning: microphone input overflow", file=sys.stderr)
                    mono = np.asarray(data, dtype=np.float32).reshape(-1)
                    chunks.append(mono.copy())
                    rms = float(np.sqrt(np.mean(np.square(mono)) + 1e-12))
                    self.write_state("recording", min(1.0, (rms ** 0.5) * 2.5))

            if self.cancelled:
                # notify("Recording cancelled.")
                return

            if not chunks:
                # notify("No audio recorded.")
                return

            audio = np.concatenate(chunks)
            if len(audio) < int(0.25 * SAMPLE_RATE):
                # notify("Recording was too short.")
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

            self.write_state("processing")
            # notify("Transcribing locally…")
            processing_start = time.perf_counter()
            model = self.load_model()
            load_seconds = time.perf_counter() - processing_start
            transcribe_start = time.perf_counter()
            lang = None if LANGUAGE.lower() == "auto" else LANGUAGE.lower()
            segments, info = model.transcribe(
                audio,  # pass the array; avoids PyAV file decoding
                language=lang,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = "".join(seg.text for seg in segments).strip()
            transcribe_seconds = time.perf_counter() - transcribe_start
            if not text:
                # notify("No speech recognized.")
                return

            # Put recognized text on the Wayland clipboard.
            # wl-copy stays alive to serve the clipboard, so don't capture its pipes.
            copier = subprocess.Popen(
                ["wl-copy", "--type", "text/plain;charset=utf-8"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            copier.stdin.write(text.encode("utf-8"))
            copier.stdin.close()
            time.sleep(0.1)
            if copier.poll() not in (None, 0):
                raise RuntimeError(f"wl-copy failed with exit code {copier.returncode}")

            # Give the clipboard manager a moment, then paste into the focused app.
            time.sleep(0.25)
            paste = run_cmd(["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"], timeout=5)
            if paste.returncode != 0:
                # notify("Text copied, but auto-paste failed. Paste with Ctrl+V.", popup=True)
                print(paste.stderr, file=sys.stderr)
            else:
                detected = getattr(info, "language", "unknown")
                # notify(f"Inserted text ({detected}): {text[:100]}")
            total_seconds = time.perf_counter() - processing_start
            print(
                f"\nTranscription: {text}\n"
                f"Timing: audio {len(audio) / SAMPLE_RATE:.1f}s | model load {load_seconds:.2f}s | "
                f"transcribe {transcribe_seconds:.2f}s | total after recording {total_seconds:.2f}s\n",
                flush=True,
            )
        except Exception as exc:
            # notify(f"Error: {exc}", popup=True)
            print(f"voice-to-text error: {exc}", file=sys.stderr, flush=True)
        finally:
            with self.lock:
                self.recording = False
                self.stop_event.clear()
            self.write_state("idle")

    def serve(self) -> None:
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o600)
        self.server.listen(8)
        print(f"Voice-to-Text daemon listening on {SOCKET_PATH}", flush=True)
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
                        result = "unknown command"
                    conn.sendall(result.encode())
                except Exception as exc:
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
        fcntl.flock(lock, fcntl.LOCK_EX)  # a second key press waits here instead of spawning another daemon
        try:
            send_command("status")
            return  # someone else started it while we waited for the lock
        except OSError:
            pass
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(APP_DIR / "daemon.log", "ab") as log:
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "daemon"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                send_command("status")
                return
            except OSError:
                if proc.poll() is not None:
                    raise OSError(f"daemon exited on startup (see {APP_DIR / 'daemon.log'})") from None
                time.sleep(0.1)
        raise TimeoutError(f"daemon did not start within {timeout:.0f}s (see {APP_DIR / 'daemon.log'})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Wayland voice-to-text")
    parser.add_argument("command", choices=["daemon", "toggle", "cancel", "status"])
    args = parser.parse_args()
    if args.command == "daemon":
        VoiceToTextDaemon().serve()
        return 0
    try:
        try:
            print(send_command(args.command))
        except OSError:
            if args.command != "toggle":
                raise
            start_daemon()
            print(send_command(args.command))
        return 0
    except OSError as exc:
        print(f"Voice-to-Text daemon is not running: {exc}", file=sys.stderr)
        print("Start it with: systemctl --user start voice-to-text.service", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
