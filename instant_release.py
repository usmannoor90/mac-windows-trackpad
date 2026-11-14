#!/usr/bin/env python3
"""
mac-windows-trackpad: Windows Precision Touchpad-style tapping for macOS.

Reads raw finger frames from Apple's private MultitouchSupport framework and
posts mouse events through CoreGraphics. Pure ctypes, no third-party packages.

  * one-finger tap        -> instant left click (no double-tap wait)
  * tap twice / 3 times   -> double / triple click
  * tap, then touch+move  -> tap-and-drag, released the instant you lift
  * two-finger tap        -> right click

Requires macOS "Tap to click" and "Use trackpad for dragging" to be OFF, and
Accessibility permission for the Python binary that runs this script.
"""

import argparse
import ctypes
import fcntl
import logging
import os
import signal
import sys
import threading
import time

__version__ = "1.7.1"

log = logging.getLogger("instant_release")

# ---------------------------------------------------------------------------
# Tuning (seconds / screen points / fraction of trackpad size)
# ---------------------------------------------------------------------------
TAP_MAX_DURATION = 0.22  # longest touch that still counts as a 1-finger tap
TAP_MAX_CURSOR_MOVE = 6.0  # px the cursor may move during a tap
TAP_MAX_FINGER_TRAVEL = 0.025  # glass travel allowed during a 1-finger tap
DOUBLE_TAP_MAX_GAP = 0.38  # max time between taps for a double/triple click
DOUBLE_TAP_MAX_SHIFT = 10.0  # px the cursor may move between those taps
# Tap, then touch down again within DRAG_ARM_WINDOW -> that touch is a
# tap-and-drag. Measured on a real trackpad: moving the cursor after a click
# never brings the finger back down sooner than 0.17 s, while a deliberate
# quick tap-tap-drag lands within 0.06-0.11 s. 0.14 s sits between them, so
# tapping something and moving the cursor never drags it. (Dragging without
# the quick rhythm: press the trackpad and drag, which is native and instant.)
# Raising this towards 0.45 gives Windows-style any-rhythm drags but also
# drags things when you tap them and move the cursor right away.
DRAG_ARM_WINDOW = 0.14
DRAG_START_MOVE = 3.0  # px of cursor movement that presses the button down
MULTITOUCH_COOLDOWN = 0.35  # ignore 1-finger taps this long after 2+ fingers
RIGHT_TAP_MAX_DURATION = 0.18  # longest 2-finger tap that right-clicks
RIGHT_TAP_MAX_FINGER_TRAVEL = 0.015  # glass travel allowed for 2-finger tap
TYPING_COOLDOWN = 0.30  # ignore taps this soon after a key press (palm guard)
WAKE_GAP = 3.0  # main-loop clock jump that means "woke from sleep"
DEVICE_WAIT_SECONDS = 30  # how long to wait for trackpad hardware per attempt

# Multitouch devices that are mice, not trackpads. Touching a Magic Mouse
# must never produce clicks.
MOUSE_FAMILY_IDS = {112, 113}

LOCK_PATH = os.path.expanduser("~/Library/Caches/instant_release.lock")

# ---------------------------------------------------------------------------
# Frameworks
# ---------------------------------------------------------------------------
multitouch = ctypes.CDLL(
    "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport"
)
cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
appservices = ctypes.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class MTPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


class MTVector(ctypes.Structure):
    _fields_ = [("position", MTPoint), ("velocity", MTPoint)]


class MTFinger(ctypes.Structure):
    _fields_ = [
        ("frame", ctypes.c_int),
        ("timestamp", ctypes.c_double),
        ("identifier", ctypes.c_int),
        ("state", ctypes.c_int),
        ("fingerId", ctypes.c_int),
        ("handId", ctypes.c_int),
        ("normalized", MTVector),  # 0.0-1.0 position on the glass
        ("size", ctypes.c_float),
        ("zero1", ctypes.c_int),
        ("angle", ctypes.c_float),
        ("majorAxis", ctypes.c_float),
        ("minorAxis", ctypes.c_float),
        ("mm", MTVector),
        ("zero2", ctypes.c_int * 2),
        ("density", ctypes.c_float),
    ]


cg.CGEventCreate.argtypes = [ctypes.c_void_p]
cg.CGEventCreate.restype = ctypes.c_void_p
cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
cg.CGEventGetLocation.restype = CGPoint
cg.CGEventCreateMouseEvent.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint32,
    CGPoint,
    ctypes.c_uint32,
]
cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
cg.CGEventSetIntegerValueField.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_int64,
]
cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
cg.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
cg.CGEventGetIntegerValueField.restype = ctypes.c_int64
TAP_CALLBACK_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p
)
cg.CGEventTapCreate.argtypes = [
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint64,
    TAP_CALLBACK_TYPE,
    ctypes.c_void_p,
]
cg.CGEventTapCreate.restype = ctypes.c_void_p
cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
cf.CFMachPortCreateRunLoopSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
cf.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
cf.CFRunLoopRun.argtypes = []
kCFRunLoopCommonModes = ctypes.c_void_p.in_dll(cf, "kCFRunLoopCommonModes")
cg.CGEventSourceButtonState.argtypes = [ctypes.c_int32, ctypes.c_uint32]
cg.CGEventSourceButtonState.restype = ctypes.c_bool
cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double

cf.CFRelease.argtypes = [ctypes.c_void_p]
cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
cf.CFArrayGetCount.restype = ctypes.c_long
cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
cf.CFDictionaryCreate.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
cf.CFDictionaryCreate.restype = ctypes.c_void_p

appservices.AXIsProcessTrusted.restype = ctypes.c_bool
appservices.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
appservices.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool

multitouch.MTDeviceCreateList.restype = ctypes.c_void_p
multitouch.MTRegisterContactFrameCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
multitouch.MTUnregisterContactFrameCallback.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
]
multitouch.MTDeviceStart.argtypes = [ctypes.c_void_p, ctypes.c_int]
multitouch.MTDeviceStop.argtypes = [ctypes.c_void_p]
multitouch.MTDeviceGetFamilyID.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
multitouch.MTDeviceIsBuiltIn.argtypes = [ctypes.c_void_p]
multitouch.MTDeviceIsBuiltIn.restype = ctypes.c_bool

kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventRightMouseDown = 3
kCGEventRightMouseUp = 4
kCGEventLeftMouseDragged = 6
kCGEventKeyDown = 10
kCGMouseButtonLeft = 0
kCGMouseButtonRight = 1
kCGHIDEventTap = 0
kCGEventSourceStateHIDSystemState = 1
kCGMouseEventClickState = 1
kCGEventSourceUserData = 42
SYNTHETIC_TAG = 0x1E57A9  # marks events this script posts, so the press guard ignores them
# Event tap (press guard)
kCGSessionEventTap = 1
kCGHeadInsertEventTap = 0
kCGEventTapOptionDefault = 0
kCGEventTapDisabledByTimeout = 0xFFFFFFFE
kCGEventTapDisabledByUserInput = 0xFFFFFFFF
kCGMouseEventDeltaX = 4
kCGMouseEventDeltaY = 5
# Shift | Control | Option | Command
MODIFIER_MASK = 0x00020000 | 0x00040000 | 0x00080000 | 0x00100000

CALLBACK_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(MTFinger),
    ctypes.c_int,
    ctypes.c_double,
    ctypes.c_int,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_cursor_pos():
    dummy = cg.CGEventCreate(None)
    loc = cg.CGEventGetLocation(dummy)
    cf.CFRelease(dummy)
    return loc


def post_mouse_event(
    event_type, loc, button=kCGMouseButtonLeft, click_state=1, delta=None
):
    ev = cg.CGEventCreateMouseEvent(None, event_type, loc, button)
    if not ev:
        return
    cg.CGEventSetIntegerValueField(ev, kCGMouseEventClickState, click_state)
    cg.CGEventSetIntegerValueField(ev, kCGEventSourceUserData, SYNTHETIC_TAG)
    # Carry held modifier keys so Cmd/Shift/Option/Ctrl + tap work like a click.
    flags = cg.CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState)
    cg.CGEventSetFlags(ev, flags & MODIFIER_MASK)
    if delta is not None:
        cg.CGEventSetIntegerValueField(ev, kCGMouseEventDeltaX, round(delta[0]))
        cg.CGEventSetIntegerValueField(ev, kCGMouseEventDeltaY, round(delta[1]))
    cg.CGEventPost(kCGHIDEventTap, ev)
    cf.CFRelease(ev)


def physical_button_down():
    """True while the trackpad is physically pressed (a real click)."""
    return cg.CGEventSourceButtonState(
        kCGEventSourceStateHIDSystemState, kCGMouseButtonLeft
    ) or cg.CGEventSourceButtonState(
        kCGEventSourceStateHIDSystemState, kCGMouseButtonRight
    )


def seconds_since_keypress():
    return cg.CGEventSourceSecondsSinceLastEventType(
        kCGEventSourceStateHIDSystemState, kCGEventKeyDown
    )


def distance(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def accessibility_trusted(prompt=False):
    """True if this process may post input events. With prompt=True macOS
    shows its "allow accessibility" dialog and adds us to the list."""
    if not prompt:
        return bool(appservices.AXIsProcessTrusted())
    try:
        key = ctypes.c_void_p.in_dll(appservices, "kAXTrustedCheckOptionPrompt")
        true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        key_cbs = ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        val_cbs = ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
        keys = (ctypes.c_void_p * 1)(key.value)
        vals = (ctypes.c_void_p * 1)(true.value)
        opts = cf.CFDictionaryCreate(
            None, keys, vals, 1, ctypes.addressof(key_cbs), ctypes.addressof(val_cbs)
        )
        trusted = bool(appservices.AXIsProcessTrustedWithOptions(opts))
        if opts:
            cf.CFRelease(opts)
        return trusted
    except (ValueError, OSError):
        return bool(appservices.AXIsProcessTrusted())


# ---------------------------------------------------------------------------
# Gesture state machine (one instance per trackpad)
# ---------------------------------------------------------------------------
class Gestures:
    def __init__(self):
        self.last_tap_up_time = 0.0
        self.last_tap_pos = None
        self.click_count = 0
        self.last_multitouch_time = 0.0
        self.is_dragging = False
        self.last_drag_pos = None
        self.reset_touch()

    def reset_touch(self):
        self.touching = False
        self.max_fingers = 0
        self.touch_start_time = 0.0
        self.touch_start_pos = None
        self.drag_start_pos = None
        self.finger_start = {}
        self.max_travel = 0.0
        self.physical_click = False  # trackpad was pressed during this touch
        self.typing = False  # touch began right after a key press

    def forget_taps(self):
        self.last_tap_up_time = 0.0
        self.last_tap_pos = None
        self.click_count = 0

    def release_drag(self):
        """Never leave the left button stuck down."""
        if self.is_dragging:
            post_mouse_event(kCGEventLeftMouseUp, get_cursor_pos())
            self.is_dragging = False
            log.debug("drag released")

    def reset(self):
        self.release_drag()
        self.reset_touch()

    def on_frame(self, fingers_ptr, n_fingers):
        now = time.time()
        if n_fingers > 0:
            self._track_travel(fingers_ptr, n_fingers)
            if not self.touching:
                self._touch_began(n_fingers, now)
            else:
                self._touch_moved(n_fingers, now)
            # A real press is handled entirely by macOS; we must not add a
            # second click (that would toggle checkboxes twice, etc.).
            if not self.is_dragging and not self.physical_click and physical_button_down():
                self.physical_click = True
                self.drag_start_pos = None
                log.debug("physical click, leaving it to macOS")
        elif self.touching:
            self._touch_ended(now)

    def _track_travel(self, fingers_ptr, n_fingers):
        for i in range(n_fingers):
            f = fingers_ptr[i]
            fx = f.normalized.position.x
            fy = f.normalized.position.y
            start = self.finger_start.get(f.identifier)
            if start is None:
                self.finger_start[f.identifier] = (fx, fy)
            else:
                travel = ((fx - start[0]) ** 2 + (fy - start[1]) ** 2) ** 0.5
                self.max_travel = max(self.max_travel, travel)

    def _touch_began(self, n_fingers, now):
        self.touching = True
        self.max_fingers = n_fingers
        self.touch_start_time = now
        self.touch_start_pos = get_cursor_pos()
        self.typing = seconds_since_keypress() < TYPING_COOLDOWN
        if (
            n_fingers == 1
            and self.click_count == 1  # no drag right after a double-click
            and not self.typing
            and (now - self.last_tap_up_time) < DRAG_ARM_WINDOW
            and (now - self.last_multitouch_time) > MULTITOUCH_COOLDOWN
        ):
            self.drag_start_pos = self.touch_start_pos

    def _touch_moved(self, n_fingers, now):
        self.max_fingers = max(self.max_fingers, n_fingers)

        if n_fingers > 1:
            # Scroll / multi-finger gesture: cancel any tap-drag in progress.
            self.last_multitouch_time = now
            self.release_drag()
            self.drag_start_pos = None
            self.forget_taps()
        elif self.max_fingers == 1:
            if self.drag_start_pos is not None and not self.is_dragging:
                elapsed = now - self.touch_start_time
                # Press the button once the finger actually moves, or is held
                # longer than a tap; lifting before that is the second tap of
                # a double-click.
                if (
                    distance(get_cursor_pos(), self.drag_start_pos) > DRAG_START_MOVE
                    or elapsed > TAP_MAX_DURATION
                ):
                    self.is_dragging = True
                    self.last_drag_pos = self.drag_start_pos
                    post_mouse_event(kCGEventLeftMouseDown, self.drag_start_pos)
                    log.debug("drag started (%.0f ms after landing)", elapsed * 1000)
            if self.is_dragging:
                cur = get_cursor_pos()
                delta = (cur.x - self.last_drag_pos.x, cur.y - self.last_drag_pos.y)
                self.last_drag_pos = cur
                post_mouse_event(kCGEventLeftMouseDragged, cur, delta=delta)

    def _touch_ended(self, now):
        cur = get_cursor_pos()
        duration = now - self.touch_start_time
        cursor_move = distance(cur, self.touch_start_pos) if self.touch_start_pos else 0.0

        if self.is_dragging:
            # The whole point: release the instant the last finger lifts.
            post_mouse_event(kCGEventLeftMouseUp, cur)
            self.is_dragging = False
            self.forget_taps()
            log.debug("drag ended")

        elif self.physical_click or self.typing:
            self.forget_taps()
            if self.typing:
                log.debug("touch ignored: typing")

        elif (
            self.max_fingers == 1
            and duration < TAP_MAX_DURATION
            and cursor_move < TAP_MAX_CURSOR_MOVE
            and self.max_travel < TAP_MAX_FINGER_TRAVEL
            and (now - self.last_multitouch_time) > MULTITOUCH_COOLDOWN
        ):
            gap = now - self.last_tap_up_time
            shift = distance(cur, self.last_tap_pos) if self.last_tap_pos else 999.0
            if gap < DOUBLE_TAP_MAX_GAP and shift < DOUBLE_TAP_MAX_SHIFT:
                self.click_count += 1  # double, triple, ... at the same spot
                pos = self.last_tap_pos
            else:
                self.click_count = 1
                pos = cur
            post_mouse_event(kCGEventLeftMouseDown, pos, click_state=self.click_count)
            post_mouse_event(kCGEventLeftMouseUp, pos, click_state=self.click_count)
            self.last_tap_up_time = now
            self.last_tap_pos = pos
            log.debug("tap x%d (%.0f ms)", self.click_count, duration * 1000)

        elif (
            self.max_fingers == 2
            and duration < RIGHT_TAP_MAX_DURATION
            and self.max_travel < RIGHT_TAP_MAX_FINGER_TRAVEL
        ):
            post_mouse_event(kCGEventRightMouseDown, cur, kCGMouseButtonRight)
            post_mouse_event(kCGEventRightMouseUp, cur, kCGMouseButtonRight)
            self.last_multitouch_time = now
            self.forget_taps()
            log.debug("right click")

        else:
            if self.max_fingers > 1:
                self.last_multitouch_time = now
            self.forget_taps()

        self.reset_touch()


# device pointer -> Gestures. Each trackpad gets its own state so two
# trackpads (built-in + Magic Trackpad) can't confuse each other.
gestures = {}
state_lock = threading.RLock()  # re-entrant: the SIGTERM handler may run while held


def _touch_callback(device, fingers_ptr, n_fingers, timestamp, frame):
    try:
        with state_lock:
            g = gestures.get(device)
            if g is not None:
                g.on_frame(fingers_ptr, n_fingers)
    except Exception:  # an exception must never escape into the C callback
        log.exception("error in touch callback")
    return 0


# Keep a reference so the C function pointer is never garbage-collected.
c_callback = CALLBACK_TYPE(_touch_callback)


# ---------------------------------------------------------------------------
# Device management
# ---------------------------------------------------------------------------
active_devices = []


def describe_device(dev):
    family = ctypes.c_int(0)
    multitouch.MTDeviceGetFamilyID(dev, ctypes.byref(family))
    builtin = bool(multitouch.MTDeviceIsBuiltIn(dev))
    is_mouse = family.value in MOUSE_FAMILY_IDS
    return family.value, builtin, is_mouse


def list_devices():
    device_list = multitouch.MTDeviceCreateList()
    count = cf.CFArrayGetCount(device_list) if device_list else 0
    return [cf.CFArrayGetValueAtIndex(device_list, i) for i in range(count)]


def stop_devices():
    for dev in active_devices:
        try:
            multitouch.MTUnregisterContactFrameCallback(dev, c_callback)
            multitouch.MTDeviceStop(dev)
        except Exception:
            pass
    del active_devices[:]
    with state_lock:
        for g in gestures.values():
            g.reset()
        gestures.clear()


def start_devices():
    """(Re)attach to every trackpad. Waits for the hardware to show up (it can
    be late at boot or after wake)."""
    stop_devices()
    for _ in range(DEVICE_WAIT_SECONDS):
        devices = list_devices()
        if devices:
            for dev in devices:
                family, builtin, is_mouse = describe_device(dev)
                kind = "built-in" if builtin else "external"
                if is_mouse:
                    log.info("skipping mouse (family %d)", family)
                    continue
                with state_lock:
                    gestures[dev] = Gestures()
                multitouch.MTRegisterContactFrameCallback(dev, c_callback)
                multitouch.MTDeviceStart(dev, 0)
                active_devices.append(dev)
                log.info("attached to %s trackpad (family %d)", kind, family)
            return bool(active_devices)
        time.sleep(1)
    log.warning("no multitouch device found after %ds", DEVICE_WAIT_SECONDS)
    return False


def acquire_single_instance_lock():
    """Two copies would double every click, so refuse to start a second one."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def shutdown(signum=None, frame=None):
    log.info("shutting down")
    stop_devices()
    sys.exit(0)


def run_check():
    trusted = accessibility_trusted(prompt=False)
    print("version:       %s" % __version__)
    print("python:        %s" % os.path.realpath(sys.executable))
    print("accessibility: %s" % ("granted" if trusted else "NOT granted"))
    trackpads = 0
    for dev in list_devices():
        family, builtin, is_mouse = describe_device(dev)
        if is_mouse:
            what = "mouse (ignored)"
        else:
            what = "built-in trackpad" if builtin else "external trackpad"
            trackpads += 1
        print("device:        %s, family %d" % (what, family))
    if not trackpads:
        print("device:        no trackpad found")
    return 0 if trusted and trackpads else 1


def any_drag_active():
    with state_lock:
        return any(g.is_dragging for g in gestures.values())


class PressGuard:
    """Event tap that swallows a real trackpad press (and its release) while a
    synthetic tap-drag is in progress. Measured on a real user: when dragging a
    window they often press the trackpad 80-150 ms after landing, i.e. after
    the synthetic drag has already started. Without this, macOS sees two
    button-downs and the real release ends the drag while the finger is still
    down. Events this script posts are tagged and always pass through."""

    def __init__(self):
        self.port = None
        self.swallow_up = False
        self._callback_ref = TAP_CALLBACK_TYPE(self._callback)  # keep alive

    def start(self):
        mask = (1 << kCGEventLeftMouseDown) | (1 << kCGEventLeftMouseUp)
        self.port = cg.CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            mask,
            self._callback_ref,
            None,
        )
        if not self.port:
            log.warning("press guard unavailable (event tap could not be created)")
            return False
        threading.Thread(target=self._run, name="press-guard", daemon=True).start()
        log.info("press guard active")
        return True

    def _run(self):
        source = cf.CFMachPortCreateRunLoopSource(None, self.port, 0)
        cf.CFRunLoopAddSource(cf.CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        cg.CGEventTapEnable(self.port, True)
        cf.CFRunLoopRun()

    def _callback(self, proxy, event_type, event, refcon):
        try:
            if event_type in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                cg.CGEventTapEnable(self.port, True)
                return event
            if cg.CGEventGetIntegerValueField(event, kCGEventSourceUserData) == SYNTHETIC_TAG:
                return event  # one of ours
            if event_type == kCGEventLeftMouseDown:
                if any_drag_active():
                    self.swallow_up = True
                    log.debug("physical press during tap-drag swallowed")
                    return None  # drop it
                self.swallow_up = False  # a real down means any earlier press has ended
            elif event_type == kCGEventLeftMouseUp and self.swallow_up:
                self.swallow_up = False
                log.debug("physical release swallowed")
                return None
        except Exception:
            log.exception("error in press guard")
        return event


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--debug", action="store_true", help="log every gesture")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check Accessibility permission and trackpads, then exit",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.check:
        sys.exit(run_check())

    lock = acquire_single_instance_lock()  # held for the life of the process
    if lock is None:
        log.error("another instance is already running; exiting")
        sys.exit(1)

    if not accessibility_trusted(prompt=True):
        log.warning(
            "Accessibility permission not granted for %s - clicks will be "
            "ignored until you allow it in System Settings > Privacy & "
            "Security > Accessibility",
            os.path.realpath(sys.executable),
        )

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("instant_release %s started (pid %d)", __version__, os.getpid())
    start_devices()
    press_guard = PressGuard()
    if accessibility_trusted():
        press_guard.start()

    # time.time() keeps counting while the Mac sleeps, so a big jump between
    # one-second ticks means we just woke up and the device handles are stale.
    last_tick = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        if (now - last_tick) > WAKE_GAP:
            log.info("wake from sleep detected (%.0fs gap), reattaching", now - last_tick)
            time.sleep(1.5)
            start_devices()
        elif not active_devices:
            start_devices()
        if press_guard.port is None and accessibility_trusted():
            press_guard.start()
        last_tick = time.time()


if __name__ == "__main__":
    main()
