#!/usr/bin/env python3
"""
mac-windows-trackpad: Windows Precision Touchpad-style tapping for macOS.

Reads raw finger frames from Apple's private MultitouchSupport framework and
posts mouse events through CoreGraphics. Pure ctypes, no third-party packages.

  * one-finger tap        -> instant left click (no double-tap wait)
  * tap twice             -> double-click
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
import tempfile
import time

__version__ = "1.1.0"

log = logging.getLogger("instant_release")

# ---------------------------------------------------------------------------
# Tuning (seconds / screen points / fraction of trackpad size)
# ---------------------------------------------------------------------------
TAP_MAX_DURATION = 0.22  # longest touch that still counts as a 1-finger tap
TAP_MAX_CURSOR_MOVE = 6.0  # px the cursor may move during a tap
TAP_MAX_FINGER_TRAVEL = 0.025  # glass travel allowed during a 1-finger tap
DOUBLE_TAP_MAX_GAP = 0.38  # max time between taps for a double-click
DOUBLE_TAP_MAX_SHIFT = 10.0  # px the cursor may move between the two taps
DRAG_ARM_WINDOW = 0.30  # touch within this long after a tap arms a drag
DRAG_START_MOVE = 3.0  # px of movement that starts the drag...
DRAG_START_HOLD = 0.13  # ...or holding this long starts it
MULTITOUCH_COOLDOWN = 0.35  # ignore 1-finger taps this long after 2+ fingers
RIGHT_TAP_MAX_DURATION = 0.18  # longest 2-finger tap that right-clicks
RIGHT_TAP_MAX_FINGER_TRAVEL = 0.015  # glass travel allowed for 2-finger tap
WAKE_GAP = 3.0  # main-loop clock jump that means "woke from sleep"
DEVICE_WAIT_SECONDS = 30  # how long to wait for trackpad hardware per attempt

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
cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

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

kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventRightMouseDown = 3
kCGEventRightMouseUp = 4
kCGEventLeftMouseDragged = 6
kCGMouseButtonLeft = 0
kCGMouseButtonRight = 1
kCGHIDEventTap = 0
kCGMouseEventClickState = 1

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


def post_mouse_event(event_type, loc, button=kCGMouseButtonLeft, click_state=1):
    ev = cg.CGEventCreateMouseEvent(None, event_type, loc, button)
    if not ev:
        return
    cg.CGEventSetIntegerValueField(ev, kCGMouseEventClickState, click_state)
    cg.CGEventPost(kCGHIDEventTap, ev)
    cf.CFRelease(ev)


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
# Gesture state machine
# ---------------------------------------------------------------------------
class Gestures:
    def __init__(self):
        self.last_tap_up_time = 0.0
        self.last_tap_pos = None
        self.last_multitouch_time = 0.0
        self.is_dragging = False
        self.reset_touch()

    def reset_touch(self):
        self.touching = False
        self.max_fingers = 0
        self.touch_start_time = 0.0
        self.touch_start_pos = None
        self.drag_start_pos = None
        self.finger_start = {}
        self.max_travel = 0.0

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
        if (
            n_fingers == 1
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
            self.last_tap_up_time = 0.0
        elif self.max_fingers == 1:
            if self.drag_start_pos is not None and not self.is_dragging:
                moved = distance(get_cursor_pos(), self.drag_start_pos)
                if (
                    moved > DRAG_START_MOVE
                    or (now - self.touch_start_time) > DRAG_START_HOLD
                ):
                    self.is_dragging = True
                    post_mouse_event(kCGEventLeftMouseDown, self.drag_start_pos)
                    log.debug("drag started")
            if self.is_dragging:
                post_mouse_event(kCGEventLeftMouseDragged, get_cursor_pos())

    def _touch_ended(self, now):
        cur = get_cursor_pos()
        duration = now - self.touch_start_time
        cursor_move = distance(cur, self.touch_start_pos) if self.touch_start_pos else 0.0

        if self.is_dragging:
            # The whole point: release the instant the last finger lifts.
            post_mouse_event(kCGEventLeftMouseUp, cur)
            self.is_dragging = False
            self.last_tap_up_time = 0.0
            self.last_tap_pos = None
            log.debug("drag ended")

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
                post_mouse_event(kCGEventLeftMouseDown, self.last_tap_pos, click_state=2)
                post_mouse_event(kCGEventLeftMouseUp, self.last_tap_pos, click_state=2)
                self.last_tap_up_time = 0.0
                self.last_tap_pos = None
                log.debug("double tap")
            else:
                post_mouse_event(kCGEventLeftMouseDown, cur)
                post_mouse_event(kCGEventLeftMouseUp, cur)
                self.last_tap_up_time = now
                self.last_tap_pos = cur
                log.debug("tap (%.0f ms)", duration * 1000)

        elif (
            self.max_fingers == 2
            and duration < RIGHT_TAP_MAX_DURATION
            and self.max_travel < RIGHT_TAP_MAX_FINGER_TRAVEL
        ):
            post_mouse_event(kCGEventRightMouseDown, cur, kCGMouseButtonRight)
            post_mouse_event(kCGEventRightMouseUp, cur, kCGMouseButtonRight)
            self.last_multitouch_time = now
            self.last_tap_up_time = 0.0
            self.last_tap_pos = None
            log.debug("right click")

        else:
            if self.max_fingers > 1:
                self.last_multitouch_time = now
            self.last_tap_up_time = 0.0
            self.last_tap_pos = None

        self.reset_touch()


gestures = Gestures()


def _touch_callback(device, fingers_ptr, n_fingers, timestamp, frame):
    try:
        gestures.on_frame(fingers_ptr, n_fingers)
    except Exception:  # an exception must never escape into the C callback
        log.exception("error in touch callback")
    return 0


# Keep a reference so the C function pointer is never garbage-collected.
c_callback = CALLBACK_TYPE(_touch_callback)


# ---------------------------------------------------------------------------
# Device management
# ---------------------------------------------------------------------------
active_devices = []


def stop_devices():
    for dev in active_devices:
        try:
            multitouch.MTUnregisterContactFrameCallback(dev, c_callback)
            multitouch.MTDeviceStop(dev)
        except Exception:
            pass
    del active_devices[:]


def start_devices():
    """(Re)attach to every multitouch device. Waits for the hardware to show
    up (it can be late at boot or after wake)."""
    stop_devices()
    gestures.reset()
    for _ in range(DEVICE_WAIT_SECONDS):
        device_list = multitouch.MTDeviceCreateList()
        count = cf.CFArrayGetCount(device_list) if device_list else 0
        if count > 0:
            for i in range(count):
                dev = cf.CFArrayGetValueAtIndex(device_list, i)
                multitouch.MTRegisterContactFrameCallback(dev, c_callback)
                multitouch.MTDeviceStart(dev, 0)
                active_devices.append(dev)
            log.info("attached to %d multitouch device(s)", count)
            return True
        time.sleep(1)
    log.warning("no multitouch device found after %ds", DEVICE_WAIT_SECONDS)
    return False


def acquire_single_instance_lock():
    """Two copies would double every click, so refuse to start a second one."""
    path = os.path.join(tempfile.gettempdir(), "instant_release.lock")
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def shutdown(signum=None, frame=None):
    log.info("shutting down")
    gestures.reset()
    stop_devices()
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--debug", action="store_true", help="log every gesture")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check Accessibility permission and trackpad, then exit",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.check:
        trusted = accessibility_trusted(prompt=False)
        device_list = multitouch.MTDeviceCreateList()
        count = cf.CFArrayGetCount(device_list) if device_list else 0
        print("python:        %s" % os.path.realpath(sys.executable))
        print("accessibility: %s" % ("granted" if trusted else "NOT granted"))
        print("trackpads:     %d multitouch device(s)" % count)
        sys.exit(0 if trusted and count else 1)

    lock = acquire_single_instance_lock()
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
        last_tick = time.time()


if __name__ == "__main__":
    main()
