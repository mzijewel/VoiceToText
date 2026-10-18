#!/usr/bin/env python3
"""Wayland-friendly local voice-to-text daemon for Linux."""
from __future__ import annotations

import argparse
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
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/voice-to-text-{os.getuid()}")) / "voice-to-text.sock"
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SECONDS = 0.25
SILENCE_SECONDS = 2.0
SILENCE_RMS = 0.012  # Raise this if room noise prevents silence detection.
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


def notify(message: str) -> None:
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=Voice to Text", "Voice to Text", message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(message, flush=True)


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
            self.stop_event = threading.Event()
            self.worker = threading.Thread(target=self.record_and_transcribe, daemon=True)
            self.worker.start()
            return "Recording started. Press the shortcut again to finish."

    def record_and_transcribe(self) -> None:
        try:
            notify("Recording… speak now. Press the shortcut to finish.")
            chunks: list[np.ndarray] = []
            silence_for = 0.0
            speech_seen = False
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
                    if rms >= SILENCE_RMS:
                        speech_seen = True
                        silence_for = 0.0
                    elif speech_seen:
                        silence_for += BLOCK_SECONDS
                        if silence_for >= SILENCE_SECONDS:
                            break

            if not chunks:
                notify("No audio recorded.")
                return

            audio = np.concatenate(chunks)
            if len(audio) < int(0.25 * SAMPLE_RATE):
                notify("Recording was too short.")
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

            notify("Transcribing locally…")
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
                notify("No speech recognized.")
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
                notify("Text copied, but auto-paste failed. Paste with Ctrl+V.")
                print(paste.stderr, file=sys.stderr)
            else:
                detected = getattr(info, "language", "unknown")
                notify(f"Inserted text ({detected}): {text[:100]}")
            total_seconds = time.perf_counter() - processing_start
            print(
                f"\nTranscription: {text}\n"
                f"Timing: audio {len(audio) / SAMPLE_RATE:.1f}s | model load {load_seconds:.2f}s | "
                f"transcribe {transcribe_seconds:.2f}s | total after recording {total_seconds:.2f}s\n",
                flush=True,
            )
        except Exception as exc:
            notify(f"Error: {exc}")
            print(f"voice-to-text error: {exc}", file=sys.stderr, flush=True)
        finally:
            with self.lock:
                self.recording = False
                self.stop_event.clear()

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
                    elif command == "status":
                        result = "recording" if self.recording else "idle"
                    else:
                        result = "unknown command"
                    conn.sendall(result.encode())
                except Exception as exc:
                    conn.sendall(f"error: {exc}".encode())


def send_command(command: str) -> str:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(command.encode())
        return client.recv(1024).decode("utf-8", "replace")
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Wayland voice-to-text")
    parser.add_argument("command", choices=["daemon", "toggle", "status"])
    args = parser.parse_args()
    if args.command == "daemon":
        VoiceToTextDaemon().serve()
        return 0
    try:
        print(send_command(args.command))
        return 0
    except (OSError, socket.timeout) as exc:
        print(f"Voice-to-Text daemon is not running: {exc}", file=sys.stderr)
        print("Start it with: systemctl --user start voice-to-text.service", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
