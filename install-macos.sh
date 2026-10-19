#!/usr/bin/env bash
# macOS installer: app + venv under ~/Library/Application Support/voice-to-text, a LaunchAgent that
# starts the daemon at login, and a 'vtt' alias. The daemon itself listens for the global hotkey.
#
# Usage: ./install-macos.sh [--key '<ctrl>+<alt>+<space>'] [--model small] [--language auto] [--no-service]
#        ./install-macos.sh --uninstall [--purge]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/voice-to-text"
VENV="$INSTALL_DIR/venv"
LABEL=local.voice-to-text
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ZSHRC="$HOME/.zshrc"

KEY=""
MODEL=""
LANGUAGE=""
DO_SERVICE=1
UNINSTALL=0
PURGE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --key) KEY="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --language) LANGUAGE="$2"; shift 2 ;;
        --no-service) DO_SERVICE=0; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --purge) PURGE=1; shift ;;
        -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$*"; }

stop_daemon() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    pkill -f "app.py daemon" 2>/dev/null || true
}

if (( UNINSTALL )); then
    step "Stopping the daemon and removing the LaunchAgent"
    stop_daemon
    rm -f "$PLIST"
    step "Removing the app from $INSTALL_DIR"
    rm -rf "$VENV"
    rm -f "$INSTALL_DIR/app.py" "$INSTALL_DIR"/daemon.log "$INSTALL_DIR"/voice-to-text.log* \
        "$INSTALL_DIR"/voice-to-text.{sock,state,lock,tmp}
    [[ -f "$ZSHRC" ]] && sed -i '' '/^# >>> voice-to-text >>>$/,/^# <<< voice-to-text <<<$/d' "$ZSHRC"
    if (( PURGE )); then
        step "Purging recordings, settings and downloaded Whisper models"
        rm -rf "$INSTALL_DIR"
        rm -rf "$HOME"/.cache/huggingface/hub/models--Systran--faster-whisper-* \
            "$HOME"/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-*
    else
        echo "Kept recordings and config.json in $INSTALL_DIR (use --purge to delete them)."
    fi
    echo "You can also remove python from Privacy & Security > Microphone / Accessibility / Input Monitoring."
    exit 0
fi

command -v python3 >/dev/null || { echo "python3 not found; install it from python.org or Homebrew." >&2; exit 1; }

step "Installing app to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
install -m 755 "$DIR/app.py" "$INSTALL_DIR/app.py"

step "Setting up Python venv and dependencies"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet faster-whisper sounddevice numpy pynput

APP=("$VENV/bin/python" "$INSTALL_DIR/app.py")
[[ -n "$KEY" ]] && "${APP[@]}" set hotkey "$KEY"
[[ -n "$MODEL" ]] && "${APP[@]}" set model "$MODEL"
[[ -n "$LANGUAGE" ]] && "${APP[@]}" set language "$LANGUAGE"

if (( DO_SERVICE )); then
    step "Installing LaunchAgent $LABEL (starts the daemon at login)"
    stop_daemon
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python</string>
        <string>$INSTALL_DIR/app.py</string>
        <string>daemon</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$INSTALL_DIR/daemon.log</string>
    <key>StandardErrorPath</key><string>$INSTALL_DIR/daemon.log</string>
</dict>
</plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
fi

step "Adding 'vtt' alias to $ZSHRC"
touch "$ZSHRC"
sed -i '' '/^# >>> voice-to-text >>>$/,/^# <<< voice-to-text <<<$/d' "$ZSHRC"  # replace an older block
cat >> "$ZSHRC" <<EOF
# >>> voice-to-text >>>
alias vtt="'$VENV/bin/python' '$INSTALL_DIR/app.py'"
# <<< voice-to-text <<<
EOF

step "Done"
PY_REAL="$("$VENV/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
cat <<EOF
Grant permissions in System Settings > Privacy & Security to:
    $PY_REAL
  - Microphone        (recording)
  - Accessibility     (pasting with Cmd+V, and the hotkey)
  - Input Monitoring  (the hotkey)
Use the + button and Cmd+Shift+G to enter the path. Then restart the daemon:
    launchctl kickstart -k gui/$(id -u)/$LABEL

Press $("${APP[@]}" settings | awk '$1 == "hotkey" {print $2}') to start recording, press again to transcribe and paste.
The first transcription downloads the Whisper model, so it will be slow.
Settings: vtt settings | vtt set language bn | vtt devices   (open a new terminal first)
Logs: tail -f "$INSTALL_DIR/voice-to-text.log"
EOF
