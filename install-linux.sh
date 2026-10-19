#!/usr/bin/env bash
# One-shot installer: system packages, app + venv under ~/.local/share/voice-to-text,
# systemd user service, GNOME global shortcut, and the on-screen overlay extension.
#
# Usage: ./install.sh [--key '<Ctrl><Alt>space'] [--model small] [--language auto]
#                     [--no-apt] [--no-service] [--no-overlay]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/share/voice-to-text"
VENV="$INSTALL_DIR/venv"
SERVICE_DIR="$HOME/.config/systemd/user"

KEY='<Ctrl><Alt>space'
MODEL=small
LANGUAGE=auto
DO_APT=1
DO_SERVICE=1
DO_OVERLAY=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --key) KEY="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --language) LANGUAGE="$2"; shift 2 ;;
        --no-apt) DO_APT=0; shift ;;
        --no-service) DO_SERVICE=0; shift ;;
        --no-overlay) DO_OVERLAY=0; shift ;;
        -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$*"; }

if [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
    echo "Warning: session type is '${XDG_SESSION_TYPE:-unknown}', not wayland; wl-copy/wtype may not work." >&2
fi

if (( DO_APT )); then
    step "Installing system packages (sudo)"
    if command -v apt-get >/dev/null; then
        sudo apt-get update
        sudo apt-get install -y python3-venv python3-dev build-essential portaudio19-dev \
            wl-clipboard wtype
    else
        echo "apt-get not found; install manually: python3-venv, portaudio, wl-clipboard, wtype" >&2
    fi
fi

step "Installing app to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
install -m 755 "$DIR/app.py" "$INSTALL_DIR/app.py"

step "Setting up Python venv and dependencies"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet faster-whisper sounddevice numpy

if (( DO_SERVICE )); then
    step "Installing systemd user service"
    mkdir -p "$SERVICE_DIR"
    sed -e "s|^Environment=VTT_MODEL=.*|Environment=VTT_MODEL=$MODEL|" \
        -e "s|^Environment=VTT_LANGUAGE=.*|Environment=VTT_LANGUAGE=$LANGUAGE|" \
        "$DIR/voice-to-text.service" > "$SERVICE_DIR/voice-to-text.service"
    # Stop any daemon left over from an earlier repo-local run so the service owns the socket.
    pkill -f "app.py daemon" 2>/dev/null || true
    systemctl --user daemon-reload
    systemctl --user enable voice-to-text.service
    systemctl --user restart voice-to-text.service
fi

step "Registering global shortcut $KEY"
if [[ -x /usr/bin/gsettings ]]; then
    VTT_PYTHON="$VENV/bin/python" VTT_APP="$INSTALL_DIR/app.py" "$DIR/install-shortcut.sh" "$KEY"
else
    echo "gsettings not found (not GNOME?); bind this command manually:" >&2
    echo "  $VENV/bin/python $INSTALL_DIR/app.py toggle" >&2
fi

step "Adding 'vtt' alias to ~/.bashrc"
BASHRC="$HOME/.bashrc"
touch "$BASHRC"
sed -i '/^# >>> voice-to-text >>>$/,/^# <<< voice-to-text <<<$/d' "$BASHRC"  # replace an older block
cat >> "$BASHRC" <<EOF
# >>> voice-to-text >>>
alias vtt="$VENV/bin/python $INSTALL_DIR/app.py"
# <<< voice-to-text <<<
EOF

if (( DO_OVERLAY )); then
    step "Installing GNOME overlay extension"
    if command -v gnome-extensions >/dev/null; then
        "$DIR/install-overlay.sh"
    else
        echo "gnome-extensions not found; skipping overlay." >&2
    fi
fi

step "Done"
echo "Press $KEY to start recording, press again to transcribe and paste."
echo "The first transcription downloads the '$MODEL' Whisper model, so it will be slow."
echo "Settings: vtt settings | vtt set language bn | vtt devices   (open a new terminal or: source ~/.bashrc)"
(( DO_SERVICE )) && echo "Logs: journalctl --user -u voice-to-text.service -f"
exit 0
