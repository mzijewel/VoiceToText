# Linux Voice-to-Text (Wayland)

Local Whisper transcription for Bangla + English, with a desktop global shortcut.

## 1. Install system packages (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential portaudio19-dev \
  wl-clipboard wtype libnotify-bin
```

Check the session:
```bash
echo "$XDG_SESSION_TYPE"
```
Expected: `wayland`.

## 2. Install the app

```bash
mkdir -p ~/.local/share/voice-to-text
cp app.py ~/.local/share/voice-to-text/app.py
python3 -m venv ~/.local/share/voice-to-text/venv
~/.local/share/voice-to-text/venv/bin/pip install --upgrade pip
~/.local/share/voice-to-text/venv/bin/pip install faster-whisper sounddevice numpy
chmod +x ~/.local/share/voice-to-text/app.py
```

The first run downloads the selected Whisper model. `small` is a practical CPU starting point. Change to `base` for lower resource use or `medium` for potentially better recognition at higher CPU/RAM cost. The model runs locally after download.

## 3. Add the user service

```bash
mkdir -p ~/.config/systemd/user
cp voice-to-text.service ~/.config/systemd/user/voice-to-text.service
systemctl --user daemon-reload
systemctl --user enable --now voice-to-text.service
systemctl --user status voice-to-text.service
```

View logs:
```bash
journalctl --user -u voice-to-text.service -f
```

## 4. Add the global shortcut

On GNOME:
1. Open **Settings → Keyboard → View and Customize Shortcuts**.
2. Find **Custom Shortcuts** (or **View and Customize Shortcuts → Custom Shortcuts**).
3. Add a shortcut named `Voice to Text`.
4. Command:
   ```bash
   /home/YOUR_USER/.local/share/voice-to-text/venv/bin/python /home/YOUR_USER/.local/share/voice-to-text/app.py toggle
   ```
   Replace `YOUR_USER` with your Linux username. You can get the full home path with `echo "$HOME"`.
5. Set the shortcut to **Super + Shift + Space** if it is available.

If your desktop's shortcut dialog supports `~`, use the full path anyway for reliability.

## 5. Use it

- Focus a text field in your app.
- Press the shortcut and speak.
- Press it again to finish, or stop speaking for about 2 seconds after speech.
- The recognized text is copied to the Wayland clipboard and pasted with Ctrl+V.

The first transcription may take longer because the model needs to download/load. Check the service logs if nothing happens.

## Configuration

Edit `~/.config/systemd/user/voice-to-text.service`, then run `systemctl --user daemon-reload` and `systemctl --user restart voice-to-text.service`.

- `VTT_MODEL=base|small|medium`
- `VTT_LANGUAGE=auto` lets Whisper detect language; use `bn` for Bangla or `en` for English.
- `VTT_COMPUTE_TYPE=int8` is intended for CPU inference.

Recording only stops when you run `toggle` a second time; there is no automatic silence detection.

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
Change `VTT_MODEL=base` in the service file for a smaller/faster model, then restart the service.

## Privacy

Audio is saved as WAV files under `~/.local/share/voice-to-text/` so you can inspect failures. Delete them when no longer needed. Transcription itself is local; the model is downloaded once from the model repository. Clipboard contents may be visible to clipboard managers.
