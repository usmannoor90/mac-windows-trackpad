# Instant Release

A tiny menu bar app that makes a Mac trackpad tap like a Windows Precision Touchpad.

- **Taps click instantly.** macOS no longer waits to see if a second tap is coming.
- **Drags let go the moment you lift your finger.** No more ~700 ms delay where macOS keeps holding what you dragged.
- **Two-finger tap right-clicks**, without misfiring at the end of a scroll.

No Python, no Terminal, no Xcode. Download, open, allow, done.

## Install

**[⬇ Download the latest release](https://github.com/usmannoor90/mac-windows-trackpad/releases/latest)** (`Instant-Release-x.y.z.zip`, about 170 KB)

1. Unzip it and drag **Instant Release.app** into **Applications**, then open it.
2. macOS will say the app is from an unidentified developer. Go to **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**. One time only. (The app is open source but not enrolled in Apple's $99/year developer program, which is what removes that warning.)
3. Click **Allow** when asked for **Accessibility** access. That's the permission that lets the app send clicks. If you missed the dialog: System Settings → Privacy & Security → Accessibility → turn on **Instant Release**.

Or paste this into Terminal, which does steps 1–2 for you:

```sh
curl -fsSL https://raw.githubusercontent.com/usmannoor90/mac-windows-trackpad/main/install-app.sh | bash
```

### Then turn off macOS's own tap features

They'd double every click, so switch them **off**. The menu bar icon shows a ⚠️ warning while either is still on, with a shortcut to the right settings page.

| Setting | Where |
|---|---|
| Tap to click | System Settings → Trackpad → Point & Click |
| Use trackpad for dragging | System Settings → Accessibility → Pointer Control → Trackpad Options |

Pressing the trackpad down still clicks as normal.

Finally, click the hand icon in the menu bar and turn on **Launch at Login**.

## Gestures

| You do | You get |
|---|---|
| One-finger quick tap | Left click, instantly |
| Two / three quick taps in the same spot | Double-click (selects a word) / triple-click (selects a line or paragraph) |
| Hold ⌘ / ⇧ / ⌥ / ⌃ while tapping | ⌘-click, ⇧-click, and so on (for example, ⌘-tap opens a link in a new tab) |
| Tap, then put your finger straight down and slide | Drag. Lifting your finger drops it immediately |
| Tap, then slide your finger as it lands | Just moves the cursor. Nothing gets dragged, so working fast after a click is safe |
| Two-finger quick tap | Right-click |
| Two-finger scroll, pinch, swipes | Unchanged, handled by macOS |
| Pressing the trackpad down (physical click) | Unchanged, handled by macOS. The app never adds a second click |

### Safeguards

- **Physical clicks are left alone.** If the trackpad is pressed during a touch, the app does nothing, so a click never counts twice.
- **Palm guard:** taps within 0.3 s of a key press are ignored, so a palm brushing the trackpad while typing doesn't move the text cursor.
- **Scroll guard:** for 0.35 s after a two-finger gesture, one-finger taps are ignored, so lifting your fingers unevenly after a scroll doesn't click.
- **Magic Mouse is skipped.** Touching its surface never produces a click.
- **No stuck button:** a drag interrupted by sleep, quitting or a second finger always releases the button.
- **Sleep/wake:** the app reconnects to the trackpad automatically after the Mac wakes.

## The menu bar icon

Click the hand icon to see the status (Running / Paused / Needs Accessibility permission), **Pause** or **Resume**, toggle **Launch at Login**, jump to any conflicting macOS setting, and **Quit**. A dimmed icon means the app isn't active (paused, no permission, or no trackpad).

## Updating

Download the new zip and replace the app in Applications, or run the Terminal one-liner again. Launch at Login and your permission carry over.

## Uninstalling

Quit the app from the menu bar, drag **Instant Release.app** to the Trash, and turn **Tap to click** and **Use trackpad for dragging** back on. Optionally remove it from Privacy & Security → Accessibility.

## Troubleshooting

**Taps do nothing.**
The app has no Accessibility permission. Open the menu bar icon; if it says *Needs Accessibility permission*, click **Allow Accessibility Access…**. If **Instant Release** is already listed in Settings but still not working (common right after updating), remove it with **−** and add it again with **+**.

**Every tap is a double-click, or text gets selected by accident.**
macOS **Tap to click** is still on. The menu shows a ⚠️ item that takes you to the setting.

**Taps feel slow again.**
**Use trackpad for dragging** is on. Turn it off.

**A tap right after typing doesn't click.**
That's the palm guard. Wait a moment after typing.

**"Instant Release.app is damaged and can't be opened."**
That's Gatekeeper, because the app isn't notarized. Either use the Terminal one-liner above, or run `xattr -dr com.apple.quarantine "/Applications/Instant Release.app"` once.

**Seeing what the app sees.**
```sh
log stream --predicate 'subsystem == "com.usmannoor90.InstantRelease"' --level debug
```
Every tap, drag and ignored touch is logged with its timing.

## Building from source

Needs macOS 13+ and Apple's Command Line Tools (`xcode-select --install`). Xcode itself is not required.

```sh
git clone https://github.com/usmannoor90/mac-windows-trackpad.git
cd mac-windows-trackpad
./build-app.sh            # runs the gesture tests, builds dist/Instant Release.app and a zip
./build-app.sh --install  # also installs to /Applications and launches it
```

The gesture engine has 19 simulated-finger tests: `swift run --package-path App GestureTests`.

### Tuning

Every timing and distance value lives in the `Tuning` enum at the top of [App/Sources/InstantReleaseCore/GestureEngine.swift](App/Sources/InstantReleaseCore/GestureEngine.swift):

| Constant | Default | Meaning |
|---|---|---|
| `tapMaxDuration` | 0.22 s | Longest touch that still counts as a tap |
| `tapMaxFingerTravel` | 0.025 | How far a finger may slide during a tap (fraction of the pad) |
| `doubleTapMaxGap` | 0.38 s | Longest gap between the taps of a double-click |
| `dragArmWindow` | 0.30 s | How soon after a tap you must touch again to start a drag |
| `dragLandingTime` | 0.06 s | After a tap, how much of the next touch's landing is inspected |
| `dragLandingTravel` | 0.012 | How far the finger may slide during that landing and still start a drag. Raise it if drags get missed; lower it if cursor moves start dragging things |
| `multitouchCooldown` | 0.35 s | Taps are ignored this long after a two-finger gesture |
| `typingCooldown` | 0.30 s | Taps are ignored this long after a key press (palm guard) |
| `rightTapMaxDuration` | 0.18 s | Longest two-finger touch that still right-clicks |

### Releasing

Bump `VERSION`, commit, then `git tag v2.1.0 && git push --tags`. GitHub Actions builds the app and attaches the zip to a new release.

## How it works

- **Raw touch data:** Apple's private `MultitouchSupport.framework` calls back on every trackpad frame with each finger's position on the glass. It's loaded at runtime with `dlopen`, so nothing private is linked at build time.
- **Gesture state machine:** each touch is tracked from first contact until all fingers lift and classified by finger count, duration, finger travel and cursor travel: tap, double-tap, drag, right-click, or "not ours". The engine is pure Swift with no AppKit, so it can be tested with fake fingers.
- **Synthetic clicks:** `CGEvent` posts mouse down/up/drag events with the right click count and the modifier keys currently held.
- **Coexisting with macOS:** physical presses, recent key presses and multi-finger gestures all turn off synthetic taps.

## Limitations

- `MultitouchSupport` is a private Apple framework. A future macOS update could break it.
- Only taps, drags and right-click are handled. Scrolling, pinch and swipes stay with macOS.
- Tested on a MacBook Pro built-in trackpad with macOS 27. It should work on macOS 13 and newer.

## Legacy script

Version 1.x was a Python script run by a LaunchAgent. It's kept in [legacy/](legacy/) for reference. The app detects an old installation and offers to remove it, since running both would double every click.

## License

[MIT](LICENSE)
