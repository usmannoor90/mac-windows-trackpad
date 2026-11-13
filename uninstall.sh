#!/bin/bash
# Stops and removes the instant_release LaunchAgent and script.
set -uo pipefail

LABEL="com.user.instantrelease"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Stopped agent."
rm -f "$PLIST" && echo "Removed $PLIST"
rm -f "$HOME/.local/bin/instant_release.py" "$HOME/.local/bin/instant_release.py.bak"
echo "Removed ~/.local/bin/instant_release.py"
rm -f "$HOME/Library/Logs/instant_release.log"

cat <<EOF

Done. To get normal macOS tapping back, turn these on again:
  System Settings > Trackpad > Point & Click > "Tap to click"
  System Settings > Accessibility > Pointer Control > Trackpad Options > "Use trackpad for dragging"
You can also remove Python from Privacy & Security > Accessibility.
EOF
