#!/usr/bin/env bash
# macOS installer: app + venv under ~/Library/Application Support/voice-to-text, a LaunchAgent that
# starts the daemon at login, and a 'vtt' alias. The daemon itself listens for the global hotkey.
#
# Usage: ./install-macos.sh [--key '<ctrl>+<alt>+<space>'] [--model base] [--language auto] [--no-service]
#        ./install-macos.sh --uninstall [--purge]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/voice-to-text"
VENV="$INSTALL_DIR/venv"
LABEL=local.voice-to-text
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BUNDLE="$INSTALL_DIR/VoiceToText.app"  # tiny wrapper so macOS lists "VoiceToText" instead of "python"
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
    rm -rf "$BUNDLE"
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

WHISPER_MODEL="$("${APP[@]}" settings | awk '$1 == "model" {print $2}')"
step "Downloading the '${WHISPER_MODEL:-base}' Whisper model (one time)"
"$VENV/bin/python" -c 'from faster_whisper.utils import download_model; import sys; download_model(sys.argv[1])' "${WHISPER_MODEL:-base}" \
    || echo "Model download failed; it will be retried on the first transcription." >&2

if (( DO_SERVICE )); then
    step "Installing LaunchAgent $LABEL (starts the daemon at login)"
    stop_daemon
    mkdir -p "$(dirname "$PLIST")" "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
    cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>VoiceToText</string>
    <key>CFBundleDisplayName</key><string>VoiceToText</string>
    <key>CFBundleIdentifier</key><string>$LABEL</string>
    <key>CFBundleExecutable</key><string>VoiceToText</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>VoiceToText records your voice to transcribe it.</string>
</dict>
</plist>
EOF
    # Native launcher embedding Python, so the process is VoiceToText.app itself (permissions are granted to it)
    PY_REAL="$("$VENV/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
    PYLIB="$(otool -L "$PY_REAL" | awk '/^\t/ && /Python/ {print $1; exit}' | sed "s#@executable_path#$(dirname "$PY_REAL")#")"
    if [[ -e "$PYLIB" ]] && clang -O1 -o "$BUNDLE/Contents/MacOS/VoiceToText" "$DIR/macos-icon/launcher.c" \
        -DPYLIB="\"$PYLIB\"" -DPYBIN="\"$VENV/bin/python\"" -DAPPPY="\"$INSTALL_DIR/app.py\""; then
        :
    else
        echo "Could not build the native launcher (needs Xcode command line tools); using a shell script." >&2
        printf '#!/bin/sh\nexec "%s" "%s" daemon\n' "$VENV/bin/python" "$INSTALL_DIR/app.py" > "$BUNDLE/Contents/MacOS/VoiceToText"
        chmod +x "$BUNDLE/Contents/MacOS/VoiceToText"
    fi
    cp "$DIR/macos-icon/AppIcon.icns" "$BUNDLE/Contents/Resources/AppIcon.icns"
    codesign --force --sign - "$BUNDLE" >/dev/null 2>&1 || true
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$BUNDLE/Contents/MacOS/VoiceToText</string>
    </array>
    <key>AssociatedBundleIdentifiers</key><string>$LABEL</string>
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
cat <<EOF
macOS prompts for permissions when the daemon starts. In System Settings > Privacy & Security, allow
"VoiceToText" (add $BUNDLE with + if missing) under:
  - Microphone        (recording)
  - Accessibility     (pasting with Cmd+V, and the hotkey)
  - Input Monitoring  (the hotkey)
Then restart the daemon:  launchctl kickstart -k gui/$(id -u)/$LABEL

Press $("${APP[@]}" settings | awk '$1 == "hotkey" {print $2}') to start recording, press again to transcribe and paste.
Settings: vtt settings | vtt set language bn | vtt devices   (open a new terminal first)
Logs: tail -f "$INSTALL_DIR/voice-to-text.log"
EOF
