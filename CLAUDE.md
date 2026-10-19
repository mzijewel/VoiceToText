# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local (offline) push-to-talk voice-to-text tool, supporting Bangla and English via `faster-whisper`. Primary target is GNOME on Wayland; macOS and Windows are also supported. A global shortcut toggles recording; on stop, audio is transcribed on CPU, put on the clipboard, and pasted into the focused app with Ctrl+V (on Linux: `wl-copy`, keys injected by the overlay extension on GNOME, `wtype` elsewhere; see "Cross-platform" below for macOS/Windows).

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
./install-macos.sh [--key K] [--model M] [--language L] [--no-service] | --uninstall [--purge]
install-windows.ps1 [-Key K] [-Model M] [-Language L] [-NoAutostart] | -Uninstall [-Purge]
```

System deps: `portaudio19-dev`, `wl-clipboard`, `wtype` (see README.md).

## Architecture

Everything in Python lives in `app.py`; one process runs as the daemon, every other invocation is a thin client.

- **Daemon (`app.py daemon`)** — `VoiceToTextDaemon` listens on a Unix socket at `$XDG_RUNTIME_DIR/voice-to-text.sock`. Plain-text commands: `toggle`, `cancel`, `status`, `settings`, `reload`. `toggle` spawns a worker thread (`record_and_transcribe`) that reads 50 ms blocks from `sounddevice` until `stop_event` is set, then saves a WAV under `~/.local/share/voice-to-text/`, transcribes (Whisper model is lazy-loaded on first use and cached), copies, and pastes. Recording only stops on a second `toggle` (no silence auto-stop).
- **Client (`app.py toggle|cancel|status`)** — `send_command` connects to the socket. If `toggle` can't connect, `start_daemon` spawns a detached daemon (logging to `~/.local/share/voice-to-text/daemon.log`) guarded by an `flock` on `voice-to-text.lock` so rapid key presses don't start duplicates, then waits for the socket.
- **Overlay (`gnome-extension/`)** — GNOME Shell extension (UUID `voice-to-text-overlay@local`, ESM, shell 46–50). It does **not** use the socket to learn state; it polls `$XDG_RUNTIME_DIR/voice-to-text.state` every 50 ms. The daemon writes that file atomically (`write_state`) as JSON `{state: idle|recording|processing|error|warning, level, message, ts}` ~20×/s while recording. `processing` carries the current step in `message` ("Loading small model…", "Transcribing… 40%", "Pasting…"), shown as a shimmering label; when the worker ends with a problem it writes `error`/`warning` with a message instead of `idle`, which the overlay shows for 4 s (click to dismiss). The overlay treats a `recording` state older than 1.5 s as stale (daemon died). Its X / ✓ buttons send `cancel` / `toggle` back over the socket. To paste, the daemon writes state `pasting` with an `id`; the overlay presses Ctrl+V (Ctrl+Shift+V when the focused window's WM_CLASS is in `TERMINAL_CLASSES`, e.g. Warp) through a Clutter virtual keyboard (Mutter lacks the virtual-keyboard protocol `wtype` needs) and replies `pasted <id>`. Without that ack within `PASTE_ACK_TIMEOUT` (1 s) the daemon falls back to `wtype`.

When changing the state file format, socket path, or command names, update both `app.py` and `gnome-extension/extension.js`.

## Cross-platform

Platform checks use `IS_LINUX` / `IS_MAC` / `IS_WINDOWS`; keep Linux behaviour unchanged when touching them.
- **Paths** (`platform_dirs`): Linux XDG dirs as above; macOS everything in `~/Library/Application Support/voice-to-text`; Windows data/runtime in `%LOCALAPPDATA%\voice-to-text`, config in `%APPDATA%\voice-to-text`.
- **IPC**: Python has no AF_UNIX on Windows, so the daemon listens on `127.0.0.1:<ephemeral>` and writes the port to `voice-to-text.port` (`connect_daemon`). The startup lock uses `msvcrt.locking` there instead of `flock`; the auto-spawned daemon uses `pythonw.exe` + `CREATE_NO_WINDOW`, and `main` redirects a missing stderr to `daemon.log`.
- **Hotkey**: GNOME binds its own shortcut, so `hotkey` defaults to `off` on Linux. Elsewhere `GlobalHotkey` runs in the daemon: Windows `RegisterHotKey` in a message-loop thread (no deps), otherwise `pynput.GlobalHotKeys` (macOS needs Accessibility + Input Monitoring). Changing `hotkey` restarts it on `reload`.
- **Clipboard/paste**: `copy_to_clipboard` (macOS `pbcopy` with `LC_CTYPE=UTF-8`, Windows Win32 `CF_UNICODETEXT` via ctypes) and `press_paste_keys` (macOS `osascript` Cmd+V, Windows `keybd_event` Ctrl+V); `paste()` skips the overlay handshake off Linux.
- **Feedback without the overlay**: `play_cue` beeps on start/stop/problem (`sounds`, default on off Linux), and error/warning notices also go to `notify(..., popup=True)` (macOS `osascript` notification, Windows PowerShell balloon).
- **Autostart**: macOS LaunchAgent `local.voice-to-text`; Windows Startup-folder shortcut to `pythonw app.py daemon`.

## Deployment modes

Two setups coexist; be aware which one a change affects:
- **Repo-local (current workflow):** `install-shortcut.sh` points the shortcut at `$REPO/.venv/bin/python $REPO/app.py toggle`, and the daemon is auto-started on first toggle.
- **systemd user service (README.md):** `voice-to-text.service` runs `~/.local/share/voice-to-text/app.py daemon` from a copied install; config is via env vars in the unit file.

## Configuration

Resolved by `load_settings()` per setting as config file (`~/.config/voice-to-text/config.json`, written by `app.py set`) > env var > default; `SETTING_SPECS` lists names, env vars, defaults and allowed values. The daemon re-reads on the `reload` socket command (sent by `set`/`reset`); a changed model/compute_type is loaded on the next transcription.

`VTT_MODEL` (default `small`; `base`/`medium`), `VTT_LANGUAGE` (`auto`, `en`, `bn`), `VTT_COMPUTE_TYPE` (default `int8`), `VTT_LOG_LEVEL` (default `DEBUG`), `VTT_DEVICE` (input device index or name; otherwise `pick_input_device` prefers the pulse/pipewire default source on Linux because raw ALSA hw devices reject 16 kHz, and the system default elsewhere), `VTT_HOTKEY`, `VTT_SOUNDS`.

## Notes

- Logging uses the `logging` module (`log = logging.getLogger("voice-to-text")`), configured by `setup_logging(role)` in `main()`. All processes (daemon and clients) append to a rotating `~/.local/share/voice-to-text/voice-to-text.log`, tagged with role/pid/thread; stderr is added only for a TTY or systemd (`JOURNAL_STREAM`) so an auto-spawned daemon doesn't duplicate lines into `daemon.log`. Use `log.*`, not `print`, for diagnostics.
- On Linux, desktop notifications (`notify-send`) have been intentionally disabled in favor of the overlay — the `notify(...)` calls in the worker are commented out; only macOS/Windows show popups (for error/warning notices). README.md still mentions `libnotify-bin`; it is no longer required.
- `install-shortcut.sh` deliberately calls `/usr/bin/gsettings` to avoid a Linuxbrew `gsettings` that may target a different dconf.
- Audio is passed to Whisper as a numpy array (not the WAV path) to avoid PyAV decoding.
- Optional LLM cleanup (`cleanup=off|ollama|claude`; legacy `on` = `ollama`): after transcription, `cleanup_text` wraps the transcript in `<transcript>` tags and sends it with `CLEANUP_PROMPT` to either Ollama (`/api/chat` via `urllib`, `think: false`, `ollama_model` default `gemma4:e4b`; `toggle` preloads it while recording) or the Claude Code CLI (`claude -p --model <claude_model>` default `haiku`, with tools/MCP/settings/skills/session persistence disabled and `cwd=APP_DIR` so no project CLAUDE.md loads). With `claude`, any Claude failure falls back to Ollama (overlay warning "Claude unavailable — used local model"): `claude_reachable` does a 2 s TCP check to `api.anthropic.com:443` first, and the CLI call is capped at `CLAUDE_TIMEOUT` (20 s) because offline it retries for minutes. `prepare_cleanup` runs that check at recording start and preloads Ollama only when Claude is unreachable. If the last provider also fails (including an output whose length ratio is outside 0.5–1.6 (model answered instead of tidying)), it falls back to the raw transcript with a `warning` overlay notice.
