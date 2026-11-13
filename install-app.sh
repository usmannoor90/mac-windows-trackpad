#!/bin/bash
# One-line installer for Instant Release:
#   curl -fsSL https://raw.githubusercontent.com/usmannoor90/mac-windows-trackpad/main/install-app.sh | bash
#
# Downloads the latest release from GitHub, puts it in /Applications, clears
# the download quarantine flag (so Gatekeeper doesn't block an unsigned app),
# and opens it. Re-run it any time to update.
set -euo pipefail

REPO="usmannoor90/mac-windows-trackpad"
APP="Instant Release.app"
DEST="/Applications/$APP"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Instant Release only runs on macOS." >&2
    exit 1
fi

echo "Finding the latest release..."
URL="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | grep '"browser_download_url"' | grep '\.zip"' | head -1 | cut -d'"' -f4)"
if [[ -z "$URL" ]]; then
    echo "Couldn't find a release download. See https://github.com/$REPO/releases" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Downloading $(basename "$URL")..."
curl -fsSL "$URL" -o "$TMP/app.zip"
ditto -x -k "$TMP/app.zip" "$TMP"
[[ -d "$TMP/$APP" ]] || { echo "Download didn't contain $APP" >&2; exit 1; }

echo "Installing to $DEST..."
osascript -e 'quit app "Instant Release"' 2>/dev/null || true
sleep 1
pkill -x InstantRelease 2>/dev/null || true
rm -rf "$DEST"
mv "$TMP/$APP" "$DEST"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

open "$DEST"
cat <<'EOF'

Instant Release is running. Look for the hand icon in the menu bar.

Two one-time steps:
  1. Click "Allow" when macOS asks for Accessibility access (or turn on
     Instant Release in System Settings > Privacy & Security > Accessibility).
  2. Turn OFF macOS's own tap features, which would double every click:
       System Settings > Trackpad > Point & Click > "Tap to click"
       System Settings > Accessibility > Pointer Control > Trackpad Options
                       > "Use trackpad for dragging"
     The menu bar icon warns you while either is still on.
EOF
