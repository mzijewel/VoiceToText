#!/usr/bin/env bash
# Removes what install.sh set up: service, GNOME shortcut, overlay extension, app + venv.
# System packages are left installed since other software may use them.
#
# Usage: ./uninstall.sh [--purge]
#   --purge  also delete saved recordings and the downloaded Whisper models
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/voice-to-text"
SERVICE="$HOME/.config/systemd/user/voice-to-text.service"
UUID=voice-to-text-overlay@local
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/voice-to-text-$(id -u)}"
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
P="$BASE/voice-to-text/"
S="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$P"
SYSTEM_GSETTINGS=/usr/bin/gsettings  # avoid the linuxbrew build, which may use a different dconf

PURGE=0
case "${1:-}" in
    --purge) PURGE=1 ;;
    "") ;;
    -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
esac

step() { printf '\n==> %s\n' "$*"; }

step "Stopping daemon and removing systemd user service"
if [[ -f "$SERVICE" ]]; then
    systemctl --user disable --now voice-to-text.service 2>/dev/null || true
    rm -f "$SERVICE"
    systemctl --user daemon-reload
fi
pkill -f "app.py daemon" 2>/dev/null || true  # auto-started or repo-local daemons
rm -f "$RUNTIME_DIR"/voice-to-text.{sock,state,tmp,lock,paste,pasted}

step "Removing GNOME shortcut"
if [[ -x "$SYSTEM_GSETTINGS" ]]; then
    CURRENT="$("$SYSTEM_GSETTINGS" get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)"
    if [[ "$CURRENT" == *"$P"* ]]; then
        # Drop our entry from the list, keeping any other custom shortcuts.
        NEW="$(python3 -c 'import ast,sys; l=[x for x in ast.literal_eval(sys.argv[1].removeprefix("@as ")) if x!=sys.argv[2]]; print(l)' "$CURRENT" "$P")"
        "$SYSTEM_GSETTINGS" set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW"
    fi
    for key in name command binding; do
        "$SYSTEM_GSETTINGS" reset "$S" "$key" 2>/dev/null || true
    done
fi

step "Removing overlay extension"
if command -v gnome-extensions >/dev/null; then
    gnome-extensions disable "$UUID" 2>/dev/null || true
fi
rm -rf "$EXT_DIR"

step "Removing app from $INSTALL_DIR"
if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR/venv" "$INSTALL_DIR/app.py" "$INSTALL_DIR/daemon.log"
    if (( PURGE )); then
        rm -f "$INSTALL_DIR"/recording-*.wav
    fi
    rmdir "$INSTALL_DIR" 2>/dev/null || echo "Kept recordings in $INSTALL_DIR (use --purge to delete)."
fi

if (( PURGE )); then
    step "Removing downloaded Whisper models"
    HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
    rm -rf "$HF_HUB"/models--Systran--faster-whisper-*
fi

step "Done"
echo "System packages (portaudio19-dev, wl-clipboard, wtype, ...) were left installed."
echo "Log out and back in to fully unload the overlay from GNOME Shell."
