#!/bin/bash
# Builds "Instant Release.app" into dist/ and zips it for distribution.
# Needs only Apple's Command Line Tools (xcode-select --install), not Xcode.
#
#   ./build-app.sh            # build + run gesture tests + bundle + zip
#   ./build-app.sh --install  # also copy to /Applications and launch
set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(cat VERSION)"
APP_NAME="Instant Release"
DIST="dist"
APP="$DIST/$APP_NAME.app"
ZIP="$DIST/Instant-Release-$VERSION.zip"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

bold "1/5  Running gesture tests"
swift run --package-path App -c release GestureTests

bold "2/5  Building"
swift build --package-path App -c release --product InstantRelease
BIN="$(swift build --package-path App -c release --show-bin-path)/InstantRelease"

bold "3/5  Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/InstantRelease"
sed "s/APP_VERSION/$VERSION/g" App/Info.plist > "$APP/Contents/Info.plist"
echo -n "APPL????" > "$APP/Contents/PkgInfo"
swift App/Tools/make-icon.swift "$APP/Contents/Resources/AppIcon.icns"
plutil -lint "$APP/Contents/Info.plist" >/dev/null

bold "4/5  Signing (ad-hoc)"
# Ad-hoc signature: enough for macOS to run it and remember its Accessibility
# permission. A Developer ID would remove the first-launch Gatekeeper warning.
codesign --force --deep --sign - --identifier com.usmannoor90.InstantRelease "$APP"
codesign --verify --deep --strict "$APP"

bold "5/5  Zipping $ZIP"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
ls -lh "$ZIP"

if [[ "${1:-}" == "--install" ]]; then
    bold "Installing to /Applications"
    osascript -e 'quit app "Instant Release"' 2>/dev/null || true
    sleep 1
    pkill -x InstantRelease 2>/dev/null || true  # in case a dialog kept it open
    rm -rf "/Applications/$APP_NAME.app"
    cp -R "$APP" "/Applications/$APP_NAME.app"
    open "/Applications/$APP_NAME.app"
    echo "Launched. Look for the hand icon in the menu bar."
fi
