#!/usr/bin/env bash
# Registers a GNOME global shortcut (Ctrl+Alt+Space) that runs `app.py toggle`.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
KEY="${1:-<Ctrl><Alt>space}"
PYTHON="${VTT_PYTHON:-$DIR/.venv/bin/python}"
APP="${VTT_APP:-$DIR/app.py}"
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
P="$BASE/voice-to-text/"
S="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$P"
SYSTEM_GSETTINGS=/usr/bin/gsettings  # avoid the linuxbrew build, which may use a different dconf

"$SYSTEM_GSETTINGS" set "$S" name 'Voice to Text'
"$SYSTEM_GSETTINGS" set "$S" command "$PYTHON $APP toggle"
"$SYSTEM_GSETTINGS" set "$S" binding "$KEY"

# Add to the existing list without dropping other custom shortcuts.
CURRENT="$("$SYSTEM_GSETTINGS" get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)"
if [[ "$CURRENT" != *"$P"* ]]; then
    if [[ "$CURRENT" == "@as []" || "$CURRENT" == "[]" ]]; then
        NEW="['$P']"
    else
        NEW="${CURRENT%]}, '$P']"
    fi
    "$SYSTEM_GSETTINGS" set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW"
fi

echo "Shortcut $KEY -> $APP toggle"
