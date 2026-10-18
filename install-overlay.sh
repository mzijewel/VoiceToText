#!/usr/bin/env bash
# Installs and enables the on-screen recording pill (GNOME Shell extension).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
UUID=voice-to-text-overlay@local
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

mkdir -p "$DEST"
cp "$DIR/gnome-extension/metadata.json" "$DIR/gnome-extension/extension.js" "$DEST/"
gnome-extensions enable "$UUID" || true

echo "Installed to $DEST"
echo "On Wayland, log out and back in once so GNOME Shell discovers the new extension."
echo "Then check: gnome-extensions info $UUID"
