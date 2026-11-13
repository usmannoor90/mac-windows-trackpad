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
| Two quick taps in the same spot | Double-click (selects a word) |
| Tap, then touch again and move | Drag. Lifting your finger drops it immediately |
| Tap, then touch and hold | Drag (for example, to start a text selection) |
| Two-finger quick tap | Right-click |
| Two-finger scroll, pinch, swipes | Unchanged, handled by macOS |

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
| `DRAG_ARM_WINDOW` | 0.30 s | How soon after a tap you must touch again to start a drag |
| `DRAG_START_HOLD` | 0.13 s | Holding this long (or moving 3 px) starts the drag |
| `MULTITOUCH_COOLDOWN` | 0.35 s | Taps are ignored this long after a scroll or other two-finger gesture |
| `RIGHT_TAP_MAX_DURATION` | 0.18 s | Longest two-finger touch that still right-clicks |

After editing, run `./install.sh` again.

## How it works

- **Raw touch data:** `MultitouchSupport.framework` (private) calls back on every trackpad frame with the position of each finger on the glass.
- **Gesture state machine:** each touch is tracked from first contact until all fingers lift. It's classified by finger count, how long it lasted, and how far the fingers and cursor moved: tap, double-tap, drag, right-click, or "not ours".
- **Synthetic clicks:** `CGEventPost` sends left/right mouse down, up and drag events. For double-clicks it also sets the click count, so word selection works.
- **Sleep/wake:** the main loop ticks once a second. A jump of more than 3 s in the clock means the Mac slept, so the script reconnects to the trackpad.
- **Safety:** if a drag is interrupted (by sleep, exit or a second finger), the button is released so it never stays stuck down.
- **Startup:** a `launchd` LaunchAgent starts the script at login and restarts it if it crashes. At boot it waits up to 30 s for the trackpad to appear.

Everything is called through `ctypes`, so there's nothing to compile. That was deliberate: `pyobjc` failed to build on some setups.

## Limitations

- `MultitouchSupport` is a private Apple framework. A future macOS update could break it.
- The script handles only taps, drags and right-click. Scrolling, pinch and swipes stay with macOS.
- Tested on a MacBook Pro built-in trackpad with macOS 27 and the system Python 3.9.

## License

[MIT](LICENSE)
