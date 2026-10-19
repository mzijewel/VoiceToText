# Linux Voice-to-Text (Wayland)

Local Whisper transcription for Bangla + English, with a desktop global shortcut.

## Install

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

## Use it

- Focus a text field in your app.
- Press the shortcut and speak.
- Press it again (or click the check mark on the overlay) to finish; click X to cancel.
- The recognized text is copied to the Wayland clipboard and pasted with Ctrl+V.

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

Re-run `./install.sh --no-apt --model ... --language ...`, or edit `~/.config/systemd/user/voice-to-text.service` and run `systemctl --user daemon-reload && systemctl --user restart voice-to-text.service`.

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
Focus a text editor before running the second command.

### Text copied but not pasted
Some Wayland compositors restrict virtual keyboard input. The text remains in the clipboard; press Ctrl+V manually. GNOME on Wayland generally needs a compatible virtual-keyboard protocol for synthetic key events. If `wtype` reports a compositor/protocol error, use manual paste or configure a compositor-supported input tool.

### No microphone permission or no input device
Check system Settings → Sound → Input. Close apps that may be holding an exclusive audio device, then restart the service.

### Model or CPU resource problems
Use a smaller/faster model: `./install.sh --no-apt --model base`.

## Privacy

Audio is saved as WAV files under `~/.local/share/voice-to-text/` so you can inspect failures. Delete them when no longer needed. Transcription itself is local; the model is downloaded once from the model repository. Clipboard contents may be visible to clipboard managers.
