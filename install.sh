#!/bin/bash
# Installs instant_release.py as a per-user LaunchAgent that starts at login.
# Safe to re-run: it replaces the old copy and restarts the agent.
set -euo pipefail

LABEL="com.user.instantrelease"
SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/instant_release.py"
INSTALL_DIR="$HOME/.local/bin"
SCRIPT_DST="$INSTALL_DIR/instant_release.py"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/instant_release.log"
PYTHON="/usr/bin/python3"
DOMAIN="gui/$(id -u)"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This only works on macOS." >&2
    exit 1
fi

if [[ ! -f "$SCRIPT_SRC" ]]; then
    echo "Can't find instant_release.py next to install.sh." >&2
    exit 1
fi

# /usr/bin/python3 is a stub until the Command Line Tools are installed.
if ! xcode-select -p >/dev/null 2>&1; then
    echo "Python 3 needs the Xcode Command Line Tools. Starting their installer..."
    xcode-select --install || true
    echo "Re-run ./install.sh once that finishes."
    exit 1
fi

bold "1/3  Copying script to $SCRIPT_DST"
mkdir -p "$INSTALL_DIR" "$(dirname "$PLIST")" "$(dirname "$LOG")"
if [[ -f "$SCRIPT_DST" ]] && ! cmp -s "$SCRIPT_SRC" "$SCRIPT_DST"; then
    cp "$SCRIPT_DST" "$SCRIPT_DST.bak"
    echo "     previous version saved as $SCRIPT_DST.bak"
fi
cp "$SCRIPT_SRC" "$SCRIPT_DST"
chmod +x "$SCRIPT_DST"
"$PYTHON" -m py_compile "$SCRIPT_DST"

bold "2/3  Writing LaunchAgent $PLIST"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DST</string>
    </array>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null

bold "3/3  (Re)starting the agent"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
sleep 1
launchctl bootstrap "$DOMAIN" "$PLIST"

REAL_PYTHON="$("$PYTHON" -c 'import os, sys; print(os.path.realpath(sys.executable))')"

echo
bold "Installed. A few one-time steps left:"
cat <<EOF

  A) Turn OFF the built-in tap features (they fight with this script):
       System Settings > Trackpad > Point & Click > "Tap to click"            -> OFF
       System Settings > Accessibility > Pointer Control > Trackpad Options
                       > "Use trackpad for dragging"                          -> OFF

  B) Allow Accessibility for Python. macOS may have just shown a prompt; if
     not, open System Settings > Privacy & Security > Accessibility, click +,
     press Cmd+Shift+G and paste:

       $REAL_PYTHON

     Make sure its switch is ON, then restart the agent:

       launchctl kickstart -k $DOMAIN/$LABEL

  C) If double-tapping folders doesn't open them, restart Finder once
     (killall Finder). The script raised the system double-click speed so
     taps count as double-clicks; apps that were already open need a restart
     to notice.

  Logs:      tail -f "$LOG"
  Uninstall: ./uninstall.sh
EOF

open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
