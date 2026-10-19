# Voice-to-Text (Linux, macOS, Windows)

Local Whisper transcription for Bangla + English, with a desktop global shortcut.

Linux (GNOME on Wayland) is the main target and has the on-screen overlay. macOS and Windows are supported too
(see [macOS](#macos) and [Windows](#windows)): there the daemon listens for the hotkey itself, beeps on
start/stop, and shows problems as a system notification instead of the overlay.

## Install (Linux)

On Ubuntu/Debian with GNOME on Wayland, run from the repo:

```bash
./install.sh
```

This will:
1. Install system packages with `sudo apt-get` (`python3-venv`, `python3-dev`, `build-essential`, `portaudio19-dev`, `wl-clipboard`, `wtype`).
2. Copy `app.py` to `~/.local/share/voice-to-text/` and create a venv there with `faster-whisper`, `sounddevice` and `numpy`.
3. Install and start the `voice-to-text.service` systemd user service.
4. Register a GNOME global shortcut (**Ctrl+Alt+Space** by default).
5. Install the on-screen overlay extension. On Wayland, log out and back in once so GNOME Shell picks it up.

Options:

```bash
./install.sh --key '<Super><Shift>space'   # different shortcut
./install.sh --model base --language bn    # Whisper model / language
./install.sh --no-apt                      # skip the sudo package install
./install.sh --no-service                  # no service; the first shortcut press starts the daemon
./install.sh --no-overlay                  # skip the GNOME overlay
```

Re-run `./install.sh --no-apt` after changing `app.py` to copy it over and restart the service.

The first transcription downloads the selected Whisper model. `small` is a practical CPU starting point; `base` uses fewer resources, `medium` may recognize better at higher CPU/RAM cost. The model runs locally after download.

View logs:
```bash
tail -f ~/.local/share/voice-to-text/voice-to-text.log   # detailed log from daemon + every toggle/cancel
journalctl --user -u voice-to-text.service -f            # same, when running as the systemd service
```

Logging defaults to `DEBUG`: device selection, mic levels every second, audio stats, model load, detected language, each Whisper segment, clipboard/paste results, timings, and full tracebacks. Set `VTT_LOG_LEVEL=INFO` (or `WARNING`) to quiet it. The file rotates at 5 MB (3 backups). `daemon.log` only catches raw stderr from an auto-started daemon (e.g. native library crashes).

If you are not on GNOME, bind this command to a shortcut in your desktop's settings:
```bash
~/.local/share/voice-to-text/venv/bin/python ~/.local/share/voice-to-text/app.py toggle
```
(use the full home path from `echo "$HOME"` if the dialog doesn't expand `~`).

## macOS

Needs Python 3.9+ (python.org or Homebrew). From the repo:

```bash
./install-macos.sh                                  # or: --key '<cmd>+<shift>+<space>' --model base --language bn
```

This copies `app.py` and a venv (`faster-whisper`, `sounddevice`, `numpy`, `pynput`) to
`~/Library/Application Support/voice-to-text/`, installs a LaunchAgent (`local.voice-to-text`) that runs the
daemon at login, and adds a `vtt` alias to `~/.zshrc`. Then, in **System Settings → Privacy & Security**, allow
the Python binary the installer prints under **Microphone**, **Accessibility** (to paste with Cmd+V) and
**Input Monitoring** (for the hotkey), and restart the daemon:
`launchctl kickstart -k gui/$(id -u)/local.voice-to-text`.

Uninstall: `./install-macos.sh --uninstall` (add `--purge` to also delete recordings, settings and models).

## Windows

Install Python 3 from python.org, then from the repo in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File install-windows.ps1     # or: -Key '<ctrl>+<shift>+<space>' -Model base -Language bn
```

This copies `app.py` and a venv to `%LOCALAPPDATA%\voice-to-text\`, adds a Startup-folder shortcut that runs
the daemon (windowless, via `pythonw`) at login, starts it, and creates `vtt.cmd` there for the commands below.
Settings live in `%APPDATA%\voice-to-text\config.json`. The hotkey uses Windows' `RegisterHotKey`, so no extra
package or permission is needed; it fails (see the log) if another app already owns that combination. Pasting
does not work into apps running as Administrator unless the daemon does too.

Uninstall: `install-windows.ps1 -Uninstall` (add `-Purge` to also delete recordings, settings and models).

### Hotkey and settings on macOS/Windows

The default hotkey is **Ctrl+Alt+Space**. Change it with `vtt set hotkey '<cmd>+<shift>+<space>'` (pynput
syntax: `<ctrl>`, `<alt>`, `<shift>`, `<cmd>`, plus a letter, digit, `<space>`, `<f1>`…); `vtt set hotkey off`
disables it. `vtt set sounds off` silences the beeps. All other `vtt` commands work as on Linux. You can also
run the daemon in the foreground with `python app.py daemon` to watch its log.

## Use it

- Focus a text field in your app.
- Press the shortcut and speak.
- Press it again (or click the check mark on the overlay) to finish; click X to cancel.
- The recognized text is copied to the clipboard and pasted with Ctrl+V (Cmd+V on macOS).

Recording only stops when you press the shortcut a second time; there is no automatic silence detection. The first transcription may take longer because the model needs to download/load. Check the service logs if nothing happens.

## Uninstall

From the repo, run:

```bash
./uninstall.sh
```

This will:
1. Stop and disable the `voice-to-text.service` user service, delete its unit file, and stop any running daemon.
2. Remove the Voice to Text GNOME shortcut, keeping your other custom shortcuts.
3. Disable and delete the overlay extension.
4. Delete `app.py`, the venv, `daemon.log` and `voice-to-text.log*` from `~/.local/share/voice-to-text/`.

Saved recordings (`recording-*.wav`) are kept by default. To also delete them and the downloaded Whisper models in `~/.cache/huggingface/hub`:

```bash
./uninstall.sh --purge
```

System packages installed by `install.sh` (`portaudio19-dev`, `wl-clipboard`, `wtype`, …) are left in place because other software may use them; remove them with `sudo apt remove` if you no longer need them. Log out and back in afterwards so GNOME Shell fully unloads the overlay.

## Configuration

On every platform, `vtt settings` / `vtt set NAME VALUE` / `vtt reset NAME` is the easiest way. On Linux you can also re-run `./install.sh --no-apt --model ... --language ...`, or edit `~/.config/systemd/user/voice-to-text.service` and run `systemctl --user daemon-reload && systemctl --user restart voice-to-text.service`.

- `VTT_MODEL=base|small|medium`
- `VTT_LANGUAGE=auto` lets Whisper detect language; use `bn` for Bangla or `en` for English.
- `VTT_COMPUTE_TYPE=int8` is intended for CPU inference.
- `VTT_DEVICE` picks a specific input device (index or name); by default the PulseAudio/PipeWire source is used.

## Troubleshooting

### Check the microphone
```bash
arecord -l
```
If `arecord` is not installed:
```bash
sudo apt install alsa-utils
```

### Test clipboard and paste support
```bash
printf 'Hello from voice to text' | wl-copy
wtype -M ctrl -P v -m ctrl
```
Focus a text editor before running the second command. (`wtype` does not work on GNOME; there the overlay extension does the paste.)

### Text copied but not pasted
GNOME on Wayland has no virtual-keyboard protocol, so `wtype` fails there ("Compositor does not support the virtual keyboard protocol"). On GNOME the overlay extension presses Ctrl+V instead: make sure it is installed and enabled (`./install-overlay.sh`, then log out and back in). `wtype` is only used as a fallback on other compositors. Either way the text remains in the clipboard; press Ctrl+V manually if pasting fails.

### No microphone permission or no input device
Check system Settings → Sound → Input. Close apps that may be holding an exclusive audio device, then restart the service.

### Model or CPU resource problems
Use a smaller/faster model: `./install.sh --no-apt --model base`.

## Privacy

Audio is saved as WAV files under `~/.local/share/voice-to-text/` so you can inspect failures. Delete them when no longer needed. Transcription itself is local; the model is downloaded once from the model repository. Clipboard contents may be visible to clipboard managers.
