# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local (offline) push-to-talk voice-to-text tool for GNOME on Wayland, supporting Bangla and English via `faster-whisper`. A global shortcut toggles recording; on stop, audio is transcribed on CPU, put on the clipboard with `wl-copy`, and pasted into the focused app with `wtype` (Ctrl+V).

## Commands

There is no build system, linter, or test suite.

```bash
# Dev environment (repo-local venv, used by run.sh and install-shortcut.sh)
python3 -m venv .venv && .venv/bin/pip install faster-whisper sounddevice numpy

./run.sh                                  # run the daemon in the foreground (logs to terminal)
.venv/bin/python app.py toggle            # start/stop recording (auto-spawns daemon if not running)
.venv/bin/python app.py cancel            # discard the current recording
.venv/bin/python app.py status            # "recording" | "idle"
.venv/bin/python app.py settings          # show effective settings (+ loaded model, last mic) from the daemon
.venv/bin/python app.py set language bn   # persist to ~/.config/voice-to-text/config.json and live-reload the daemon
.venv/bin/python app.py reset language    # drop a saved value (env var / default applies again)
.venv/bin/python app.py devices           # list input devices for `set device`

./install.sh [--key K] [--model M] [--language L] [--no-apt|--no-service|--no-overlay]
                                          # full install: apt deps, ~/.local/share copy + venv, service, shortcut, overlay
./uninstall.sh [--purge]                  # reverse install.sh; --purge also removes recordings + HF model cache
./install-shortcut.sh ['<Ctrl><Alt>space']  # register GNOME custom keybinding -> app.py toggle (VTT_PYTHON/VTT_APP override paths)
./install-overlay.sh                      # install/enable the GNOME Shell extension (needs re-login on Wayland)
```

System deps: `portaudio19-dev`, `wl-clipboard`, `wtype` (see README.md).

## Architecture

Everything in Python lives in `app.py`; one process runs as the daemon, every other invocation is a thin client.

- **Daemon (`app.py daemon`)** — `VoiceToTextDaemon` listens on a Unix socket at `$XDG_RUNTIME_DIR/voice-to-text.sock`. Plain-text commands: `toggle`, `cancel`, `status`, `settings`, `reload`. `toggle` spawns a worker thread (`record_and_transcribe`) that reads 50 ms blocks from `sounddevice` until `stop_event` is set, then saves a WAV under `~/.local/share/voice-to-text/`, transcribes (Whisper model is lazy-loaded on first use and cached), copies, and pastes. Recording only stops on a second `toggle` (no silence auto-stop).
- **Client (`app.py toggle|cancel|status`)** — `send_command` connects to the socket. If `toggle` can't connect, `start_daemon` spawns a detached daemon (logging to `~/.local/share/voice-to-text/daemon.log`) guarded by an `flock` on `voice-to-text.lock` so rapid key presses don't start duplicates, then waits for the socket.
- **Overlay (`gnome-extension/`)** — GNOME Shell extension (UUID `voice-to-text-overlay@local`, ESM, shell 46–50). It does **not** use the socket to learn state; it polls `$XDG_RUNTIME_DIR/voice-to-text.state` every 50 ms. The daemon writes that file atomically (`write_state`) as JSON `{state: idle|recording|processing|error|warning, level, message, ts}` ~20×/s while recording. `processing` carries the current step in `message` ("Loading small model…", "Transcribing… 40%", "Pasting…"), shown as a shimmering label; when the worker ends with a problem it writes `error`/`warning` with a message instead of `idle`, which the overlay shows for 4 s (click to dismiss). The overlay treats a `recording` state older than 1.5 s as stale (daemon died). Its X / ✓ buttons send `cancel` / `toggle` back over the socket.

When changing the state file format, socket path, or command names, update both `app.py` and `gnome-extension/extension.js`.

## Deployment modes

Two setups coexist; be aware which one a change affects:
- **Repo-local (current workflow):** `install-shortcut.sh` points the shortcut at `$REPO/.venv/bin/python $REPO/app.py toggle`, and the daemon is auto-started on first toggle.
- **systemd user service (README.md):** `voice-to-text.service` runs `~/.local/share/voice-to-text/app.py daemon` from a copied install; config is via env vars in the unit file.

## Configuration

Resolved by `load_settings()` per setting as config file (`~/.config/voice-to-text/config.json`, written by `app.py set`) > env var > default; `SETTING_SPECS` lists names, env vars, defaults and allowed values. The daemon re-reads on the `reload` socket command (sent by `set`/`reset`); a changed model/compute_type is loaded on the next transcription.

`VTT_MODEL` (default `small`; `base`/`medium`), `VTT_LANGUAGE` (`auto`, `en`, `bn`), `VTT_COMPUTE_TYPE` (default `int8`), `VTT_LOG_LEVEL` (default `DEBUG`), `VTT_DEVICE` (input device index or name; otherwise `pick_input_device` prefers the pulse/pipewire default source because raw ALSA hw devices reject 16 kHz).

## Notes

- Logging uses the `logging` module (`log = logging.getLogger("voice-to-text")`), configured by `setup_logging(role)` in `main()`. All processes (daemon and clients) append to a rotating `~/.local/share/voice-to-text/voice-to-text.log`, tagged with role/pid/thread; stderr is added only for a TTY or systemd (`JOURNAL_STREAM`) so an auto-spawned daemon doesn't duplicate lines into `daemon.log`. Use `log.*`, not `print`, for diagnostics.
- Desktop notifications (`notify-send`) have been intentionally disabled in favor of the overlay — the `notify(...)` calls in the worker are commented out. README.md still mentions `libnotify-bin`; it is no longer required.
- `install-shortcut.sh` deliberately calls `/usr/bin/gsettings` to avoid a Linuxbrew `gsettings` that may target a different dconf.
- Audio is passed to Whisper as a numpy array (not the WAV path) to avoid PyAV decoding.
