# mac-windows-trackpad

Make a MacBook trackpad tap like a Windows Precision Touchpad:

- **Taps click instantly.** macOS no longer waits to see if a second tap is coming.
- **Tap-and-drag lets go the moment you lift your finger.** No more ~700 ms delay where macOS keeps holding the drag.
- **Two-finger tap right-clicks.** It doesn't misfire at the end of a scroll.

It's one Python file. It uses only the Python that comes with macOS and needs no `pip install`.

## Install

You need a Mac with a built-in or Magic Trackpad.

```sh
git clone https://github.com/usmannoor90/mac-windows-trackpad.git
cd mac-windows-trackpad
./install.sh
```

The installer copies the script to `~/.local/bin/`, sets it up to start every time you log in, and starts it now. If Apple's Command Line Tools (which include Python 3) aren't installed, it starts their installer. Run `./install.sh` again once that finishes.

Then do these **two one-time steps**:

### 1. Turn off the macOS tap features

They conflict with the script, so turn them off.

| Setting | Where | Set to |
|---|---|---|
| Tap to click | System Settings → Trackpad → Point & Click | **Off** |
| Use trackpad for dragging | System Settings → Accessibility → Pointer Control → Trackpad Options | **Off** |

You can still press down on the trackpad to physically click, as before.

### 2. Allow Accessibility access for Python

The script has to post mouse clicks, which macOS only allows for apps with Accessibility permission.

1. Open **System Settings → Privacy & Security → Accessibility**. The installer opens this page for you.
2. If **python3** is already listed, turn it **on**. If not, click **+**, press **⌘⇧G**, paste the Python path the installer printed (it looks like `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`), and add it.
3. Restart the script:

   ```sh
   launchctl kickstart -k gui/$(id -u)/com.user.instantrelease
   ```

Now tap something. It should click instantly.

## Gestures

| You do | You get |
|---|---|
| One-finger quick tap | Left click, instantly |
| Two / three quick taps in the same spot | Double-click (selects a word) / triple-click (selects a line or paragraph) |
| Hold ⌘ / ⇧ / ⌥ / ⌃ while tapping | Same as ⌘-click, ⇧-click, and so on (for example, ⌘-tap opens a link in a new tab) |
| Tap, then put your finger back down **right away** (within 0.14 s, a quick tap-tap rhythm) and slide | Drag. Lifting your finger drops it immediately |
| Press the trackpad down and move | Drag, handled by macOS, also released instantly |
| Tap, then touch again a little later (0.17 s or more) and move | Just moves the cursor. Nothing is dragged, so tapping a chess piece or a tab and then moving the cursor is always safe |
| Two-finger quick tap | Right-click |
| Two-finger scroll, pinch, swipes | Unchanged, handled by macOS |
| Pressing the trackpad down (a physical click) | Unchanged, handled by macOS. The script never adds a second click |
| Pressing the trackpad while a tap-drag is already in progress | Ignored. The drag continues until you lift your finger |

### Safeguards against interfering with macOS

- **Physical clicks are left alone.** If the trackpad is pressed during a touch, the script does nothing, so a click never counts twice (a checkbox won't toggle on and back off).
- **Palm guard:** taps within 0.3 s of a key press are ignored, so resting your palm while typing doesn't move the text cursor.
- **Scroll guard:** for 0.35 s after a two-finger gesture, one-finger taps are ignored, so lifting your fingers unevenly after a scroll doesn't click.
- **Phantom contacts ignored:** the trackpad sometimes reports a second contact for a frame or two as a finger lands. A touch only counts as two-finger after 3 frames (24 ms), so a tap never turns into a right-click.
- **Magic Mouse is skipped.** Only trackpads are used. Touching a Magic Mouse's surface never produces a click.
- **No stuck button:** a drag interrupted by sleep, quitting or a second finger always releases the button.
- **Press guard:** a real press during a tap-drag is intercepted, so it can't end or restart the drag. (Many people press down while dragging windows; without this, macOS sees two button-downs and drops the window when the pressure eases.)
- **Load-proof timing:** gestures are timed with the trackpad's own hardware timestamps and the script runs at user-interactive priority, so a busy Mac (video call, build, heavy browser tab) can't turn a quick tap into an ignored long press.
- **Only one copy runs at a time**, so clicks are never doubled by a duplicate process.

## Everyday commands

```sh
# Is it running?
launchctl print gui/$(id -u)/com.user.instantrelease | grep state

# Restart it
launchctl kickstart -k gui/$(id -u)/com.user.instantrelease

# Watch the log
tail -f ~/Library/Logs/instant_release.log

# Check permission and trackpad detection
/usr/bin/python3 ~/.local/bin/instant_release.py --check
```

### Updating

```sh
cd mac-windows-trackpad
git pull
./install.sh
```

The installer keeps your previous version as `~/.local/bin/instant_release.py.bak`.

### Uninstalling

```sh
./uninstall.sh
```

Then turn **Tap to click** and **Use trackpad for dragging** back on.

## Troubleshooting

**Taps do nothing.**
Python doesn't have Accessibility permission. Check step 2, then restart the script. `~/Library/Logs/instant_release.log` will contain `Accessibility permission not granted`.

**Every tap is a double-click, or text gets selected by accident.**
macOS **Tap to click** is still on, so each tap clicks twice. Turn it off.

**A tap right after typing doesn't click.**
That's the palm guard. Wait a moment after typing, or lower `TYPING_COOLDOWN`.

**A device is ignored or used by mistake.**
Run `/usr/bin/python3 ~/.local/bin/instant_release.py --check` to list the devices the script found and their family IDs. Mice are skipped by family ID (`MOUSE_FAMILY_IDS`). Please open an issue if your device is classified wrong.

**Tap-drag only starts sometimes.**
The second touch has to land within 0.14 s of the tap, a quick tap-*tap* rhythm. Later than that, the touch is treated as moving the cursor, because that is exactly what "tap something, then move the cursor" looks like and the two can't be told apart. To drag without the rhythm, press the trackpad down and drag: native, and also released instantly.

**Taps feel slow again.**
**Use trackpad for dragging** is on. Turn it off.

**It stopped working after the Mac woke from sleep.**
The script reconnects to the trackpad automatically a couple of seconds after wake. If it still doesn't work, run the restart command above and look at the log.

**Testing changes by hand.**
Stop the background copy first, or every click will happen twice. The script refuses to start a second copy anyway.

```sh
launchctl bootout gui/$(id -u)/com.user.instantrelease
/usr/bin/python3 instant_release.py --debug     # logs every gesture; Ctrl+C to stop
./install.sh                                    # put the background copy back
```

## Tuning

All the timing and distance values are constants near the top of [instant_release.py](instant_release.py):

| Constant | Default | Meaning |
|---|---|---|
| `TAP_MAX_DURATION` | 0.22 s | Longest touch that still counts as a tap |
| `TAP_MAX_FINGER_TRAVEL` | 0.025 | How far a finger may slide during a tap (fraction of the pad) |
| `DOUBLE_TAP_MAX_GAP` | 0.38 s | Longest gap between the two taps of a double-click |
| `DRAG_ARM_WINDOW` | 0.14 s | How soon after a tap the finger must come back down to start a drag. Measured: quick tap-tap-drags land within 0.06–0.11 s, cursor moves after a click land after 0.17 s. Raise it towards 0.45 for Windows-style any-rhythm drags, at the cost of dragging things when you tap them and move the cursor right away |
| `MULTITOUCH_COOLDOWN` | 0.35 s | Taps are ignored this long after a scroll or other two-finger gesture |
| `TYPING_COOLDOWN` | 0.30 s | Taps are ignored this long after a key press (palm guard) |
| `DOUBLE_TAP_MAX_FINGER_TRAVEL` | 0.08 | How far the finger may slide during the second tap of a double-tap (the cursor must still not move) |
| `MULTI_MIN_FRAMES` | 3 | Frames (8 ms each) with two contacts before a touch counts as two-finger; filters phantom contacts |
| `RIGHT_TAP_MAX_DURATION` | 0.25 s | Longest two-finger touch that still right-clicks |
| `RIGHT_TAP_MAX_CENTROID_TRAVEL` | 0.03 | How far the midpoint between the two fingers may move during a two-finger tap |

After editing, run `python3 tests/test_gestures.py` (22 simulated-finger tests, nothing is posted to the system), then `./install.sh` again.

## How it works

- **Raw touch data:** `MultitouchSupport.framework` (private) calls back on every trackpad frame with the position of each finger on the glass.
- **Gesture state machine:** each touch is tracked from first contact until all fingers lift. It's classified by finger count, how long it lasted, and how far the fingers and cursor moved: tap, double-tap, drag, right-click, or "not ours".
- **Synthetic clicks:** `CGEventPost` sends left/right mouse down, up and drag events. For double-clicks it also sets the click count, so word selection works.
- **Sleep/wake:** the main loop ticks once a second. A jump of more than 3 s in the clock means the Mac slept, so the script reconnects to the trackpad.
- **Coexisting with macOS:** physical presses (button state from the HID system), recent key presses and multi-finger gestures all turn off synthetic taps. Held modifier keys are copied onto every synthetic click. Each trackpad has its own gesture state.
- **Startup:** a `launchd` LaunchAgent starts the script at login and restarts it if it crashes. At boot it waits up to 30 s for the trackpad to appear.

Everything is called through `ctypes`, so there's nothing to compile. That was deliberate: `pyobjc` failed to build on some setups.

## Limitations

- `MultitouchSupport` is a private Apple framework. A future macOS update could break it.
- The script handles only taps, drags and right-click. Scrolling, pinch and swipes stay with macOS.
- Tested on a MacBook Pro built-in trackpad with macOS 27 and the system Python 3.9.

## License

[MIT](LICENSE)
