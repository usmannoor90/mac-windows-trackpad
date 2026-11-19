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
import subprocess
import sys
import threading
import time

__version__ = "1.15.0"

log = logging.getLogger("instant_release")

# ---------------------------------------------------------------------------
# Tuning (seconds / screen points / fraction of trackpad size)
# ---------------------------------------------------------------------------
TAP_MAX_DURATION = 0.30  # longest touch that still counts as a 1-finger tap (measured slow taps: 0.28 s)
TAP_MAX_CURSOR_MOVE = 6.0  # px the cursor may move during a tap
# macOS keeps the cursor still for a tap even when the finger slides a little,
# so the cursor-move limit above is the real guard. Measured sloppy taps slide
# 3-8% of the pad; this limit only rejects outright swipes.
TAP_MAX_FINGER_TRAVEL = 0.08
# The 2nd/3rd tap of a double/triple-tap tends to be sloppier (a small flick on
# the glass) while macOS still keeps the cursor still; allow more travel then.
DOUBLE_TAP_MAX_FINGER_TRAVEL = 0.10
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
DRAG_START_MOVE = 6.0  # px of cursor movement that presses the button down (= tap wobble limit)
MULTITOUCH_COOLDOWN = 0.35  # ignore 1-finger taps this long after 2+ fingers
RIGHT_TAP_MAX_DURATION = 0.25  # longest 2-finger tap that right-clicks
RIGHT_TAP_MAX_CENTROID_TRAVEL = 0.03  # how far the midpoint of the 2 fingers may move
# The trackpad often reports a phantom second contact for 1-2 frames as a
# finger lands. A touch counts as multi-finger only after this many frames
# (8 ms each) with 2+ contacts; a real two-finger tap shows 20-30.
MULTI_MIN_FRAMES = 3
TYPING_COOLDOWN = 0.30  # ignore taps this soon after a key press (palm guard)
WAKE_GAP = 3.0  # main-loop clock jump that means "woke from sleep"
DEVICE_WAIT_SECONDS = 30  # how long to wait for trackpad hardware per attempt
# A tap clicks when the finger lifts, so the two clicks of a double-tap land
# 0.15-0.25 s apart (measured), slower than a physical double-click. Finder
# ignores a double-click slower than the system "Double-click speed", and at
# the fastest setting (0.15 s) most double-taps on folders fail. On start the
# system setting is raised to at least this many seconds (Windows default:
# 0.5). Set to None to leave the system setting alone.
MIN_DOUBLE_CLICK_TIME = 0.5

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
iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
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
cg.CGEventGetFlags.argtypes = [ctypes.c_void_p]
cg.CGEventGetFlags.restype = ctypes.c_uint64
appservices.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
appservices.AXUIElementCreateApplication.restype = ctypes.c_void_p
appservices.AXUIElementCopyAttributeValue.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
appservices.AXUIElementCopyAttributeValue.restype = ctypes.c_int
appservices.AXUIElementSetMessagingTimeout.argtypes = [ctypes.c_void_p, ctypes.c_float]
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
cf.CFGetTypeID.restype = ctypes.c_ulong
cf.CFStringGetTypeID.restype = ctypes.c_ulong
cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
cf.CFBooleanGetValue.restype = ctypes.c_bool
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
cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
cf.CFMachPortInvalidate.argtypes = [ctypes.c_void_p]
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

# Finder, Windows style: Return opens the selection, Delete/Backspace goes back.
# Only when Finder is frontmost, no modifier is held, and keyboard focus is on
# a file view (never while renaming, in the search box, or in a dialog).
FINDER_WINDOWS_KEYS = True
kCGEventKeyUp = 11
kCGKeyboardEventKeycode = 9
kCGEventFlagMaskCommand = 0x00100000
kCFStringEncodingUTF8 = 0x08000100
KEY_RETURN = 36
KEY_DELETE = 51  # the Mac "delete" key = Backspace
KEY_DOWN_ARROW = 125
KEY_LEFT_BRACKET = 33
FINDER_KEY_MAP = {KEY_RETURN: KEY_DOWN_ARROW, KEY_DELETE: KEY_LEFT_BRACKET}  # + Command
FINDER_FILE_VIEW_ROLES = ("AXList", "AXOutline", "AXBrowser", "AXTable")
# Every key press and click passes through this script's event tap before any
# app sees it, so the tap callback must never wait on anything. Whether Finder
# is frontmost with a file view focused is polled on a background thread this
# often; the callback only reads the result.
FINDER_POLL_SECONDS = 0.1
# If macOS reports the tap as too slow this many times within the window, the
# script stops intercepting keys (and then clicks) instead of re-arming it, so
# it can never make the keyboard or trackpad laggy.
TAP_TIMEOUT_LIMIT = 2
TAP_TIMEOUT_WINDOW = 120.0
PRESS_GUARD_RETRY_SECONDS = 600.0
# Seen in the wild: after macOS timed the tap out (an app such as Shortcuts
# opening), the process could no longer create a tap *or* post clicks, while
# a freshly started process could. If a tap that worked before can't be
# recreated this many times in a row, the script exits and launchd
# (KeepAlive) starts a fresh copy a few seconds later.
TAP_RECREATE_FAILURES_BEFORE_RESTART = 3
# While a finger is on the pad the trackpad reports ~125 frames a second. If
# they stop mid-touch (a system dialog grabbed input, the device reset), the
# lift will never arrive: after this long the touch is ended and any drag
# released, so the button can never stay stuck down.
STALE_TOUCH_SECONDS = 1.0
# The press guard only swallows a real press if the drag got a frame this
# recently, i.e. a finger is verifiably still dragging.
PRESS_GUARD_FRESH_SECONDS = 0.15

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
        self.last_frame_mono = 0.0  # time.monotonic() of the latest frame
        self.reset_touch()

    def reset_touch(self):
        self.touching = False
        self.max_fingers = 0
        self.touch_start_time = 0.0
        self.touch_start_pos = None
        self.drag_start_pos = None
        self.finger_start = {}
        self.max_travel = 0.0
        self.multi_frames = 0  # frames seen with 2+ contacts
        self.centroid_start = None
        self.max_centroid_travel = 0.0
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

    def on_frame(self, fingers_ptr, n_fingers, now=None):
        # `now` is the trackpad's own frame timestamp (seconds, 125 Hz). Using
        # it instead of time.time() keeps tap durations and gaps correct even
        # when a busy system delivers the frames to us late.
        if now is None:
            now = time.time()
        self.last_frame_mono = time.monotonic()
        if n_fingers > 0:
            self._track_travel(fingers_ptr, n_fingers)
            if n_fingers > 1:
                self.multi_frames += 1
            # A brief phantom second contact counts as one finger.
            effective = n_fingers if n_fingers == 1 or self.multi_frames >= MULTI_MIN_FRAMES else 1
            if not self.touching:
                self._touch_began(effective, now)
            else:
                self._touch_moved(effective, now)
            # A real press is handled entirely by macOS; we must not add a
            # second click (that would toggle checkboxes twice, etc.).
            if not self.is_dragging and not self.physical_click and physical_button_down():
                self.physical_click = True
                self.drag_start_pos = None
                log.debug("physical click, leaving it to macOS")
        elif self.touching:
            self._touch_ended(now)

    def _track_travel(self, fingers_ptr, n_fingers):
        cx = cy = 0.0
        for i in range(n_fingers):
            f = fingers_ptr[i]
            fx = f.normalized.position.x
            fy = f.normalized.position.y
            cx += fx
            cy += fy
            start = self.finger_start.get(f.identifier)
            if start is None:
                self.finger_start[f.identifier] = (fx, fy)
            else:
                travel = ((fx - start[0]) ** 2 + (fy - start[1]) ** 2) ** 0.5
                self.max_travel = max(self.max_travel, travel)
        if n_fingers > 1:
            # Midpoint travel: immune to the trackpad swapping finger IDs,
            # which makes per-finger travel jump by the finger spacing.
            cx /= n_fingers
            cy /= n_fingers
            if self.centroid_start is None:
                self.centroid_start = (cx, cy)
            else:
                d = ((cx - self.centroid_start[0]) ** 2 + (cy - self.centroid_start[1]) ** 2) ** 0.5
                self.max_centroid_travel = max(self.max_centroid_travel, d)

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

    def _follows_tap(self, now, cur):
        """True if this touch is a candidate 2nd/3rd tap of a multi-click."""
        if self.last_tap_pos is None:
            return False
        return (
            now - self.last_tap_up_time < DOUBLE_TAP_MAX_GAP
            and distance(cur, self.last_tap_pos) < DOUBLE_TAP_MAX_SHIFT
        )

    def _tap_travel_limit(self, now, cur):
        return DOUBLE_TAP_MAX_FINGER_TRAVEL if self._follows_tap(now, cur) else TAP_MAX_FINGER_TRAVEL

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
            and self.max_travel < self._tap_travel_limit(now, cur)
            and (now - self.last_multitouch_time) > MULTITOUCH_COOLDOWN
        ):
            if self._follows_tap(now, cur):
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
            and self.max_centroid_travel < RIGHT_TAP_MAX_CENTROID_TRAVEL
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


_libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
_libsystem.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint, ctypes.c_int]
QOS_CLASS_USER_INTERACTIVE = 0x21
_boosted_threads = set()


def boost_current_thread():
    """Ask the scheduler to treat this thread as user-interactive, so a busy
    system (video call, build, browser) can't delay touch processing."""
    tid = threading.get_ident()
    if tid in _boosted_threads:
        return
    _boosted_threads.add(tid)
    try:
        _libsystem.pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0)
    except Exception:
        pass


def _touch_callback(device, fingers_ptr, n_fingers, timestamp, frame):
    try:
        boost_current_thread()
        with state_lock:
            g = gestures.get(device)
            if g is not None:
                g.on_frame(fingers_ptr, n_fingers, timestamp)
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
    dct = double_click_time()
    if dct is not None:
        print("double-click:  %.2f s%s" % (
            dct, "  (too fast for double-taps; restart the agent to raise it)"
            if MIN_DOUBLE_CLICK_TIME and dct < MIN_DOUBLE_CLICK_TIME - 0.001 else ""))
    return 0 if trusted and trackpads else 1


DOUBLE_CLICK_DEFAULTS_KEY = "com.apple.mouse.doubleClickThreshold"
HID_CLICK_TIME_KEY = b"HIDClickTime"  # nanoseconds, what apps actually use
kIOHIDEventSystemClientTypeMonitor = 1
kCFNumberSInt64Type = 4


def _hid_click_time_property(value_ns=None):
    """Read (or, with value_ns, set) the live system double-click time.
    Returns the value in seconds, or None if it can't be read."""
    V = ctypes.c_void_p
    iokit.IOHIDEventSystemClientCreateWithType.argtypes = [V, ctypes.c_int, V]
    iokit.IOHIDEventSystemClientCreateWithType.restype = V
    iokit.IOHIDEventSystemClientCopyProperty.argtypes = [V, V]
    iokit.IOHIDEventSystemClientCopyProperty.restype = V
    iokit.IOHIDEventSystemClientSetProperty.argtypes = [V, V, V]
    iokit.IOHIDEventSystemClientSetProperty.restype = ctypes.c_bool
    cf.CFStringCreateWithCString.argtypes = [V, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = V
    cf.CFNumberCreate.argtypes = [V, ctypes.c_int, V]
    cf.CFNumberCreate.restype = V
    cf.CFNumberGetValue.argtypes = [V, ctypes.c_int, V]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    client = iokit.IOHIDEventSystemClientCreateWithType(
        None, kIOHIDEventSystemClientTypeMonitor, None
    )
    if not client:
        return None
    key = cf.CFStringCreateWithCString(None, HID_CLICK_TIME_KEY, kCFStringEncodingUTF8)
    try:
        if value_ns is not None:
            v = ctypes.c_int64(int(value_ns))
            num = cf.CFNumberCreate(None, kCFNumberSInt64Type, ctypes.byref(v))
            iokit.IOHIDEventSystemClientSetProperty(client, key, num)
            cf.CFRelease(num)
        ref = iokit.IOHIDEventSystemClientCopyProperty(client, key)
        if not ref:
            return None
        out = ctypes.c_int64()
        ok = cf.CFNumberGetValue(ref, kCFNumberSInt64Type, ctypes.byref(out))
        cf.CFRelease(ref)
        return out.value / 1e9 if ok else None
    finally:
        cf.CFRelease(key)
        cf.CFRelease(client)


def double_click_time():
    try:
        return _hid_click_time_property()
    except Exception:
        return None


def ensure_double_click_time():
    """Raise the system double-click speed to MIN_DOUBLE_CLICK_TIME if it is
    faster, both live (HID system) and saved (so it survives a restart)."""
    if not MIN_DOUBLE_CLICK_TIME:
        return
    try:
        current = _hid_click_time_property()
        if current is None or current >= MIN_DOUBLE_CLICK_TIME - 0.001:
            return
        new = _hid_click_time_property(MIN_DOUBLE_CLICK_TIME * 1e9)
        subprocess.run(
            ["/usr/bin/defaults", "write", "-g", DOUBLE_CLICK_DEFAULTS_KEY,
             "-float", str(MIN_DOUBLE_CLICK_TIME)],
            check=False, timeout=5,
        )
        log.info(
            "double-click speed was %.2f s, too fast for double-taps; set it to "
            "%.2f s (apps that were already open, like Finder, use the new "
            "value after they restart)", current, new if new else MIN_DOUBLE_CLICK_TIME,
        )
    except Exception as exc:
        log.warning("could not adjust double-click speed: %s", exc)


def any_drag_active():
    """Called from the event tap, so it takes no lock: the touch thread may be
    holding state_lock while it waits on the window server, which is itself
    waiting for the tap to answer. Plain attribute reads are safe under the GIL."""
    now = time.monotonic()
    for g in list(gestures.values()):
        if g.is_dragging and now - g.last_frame_mono < PRESS_GUARD_FRESH_SECONDS:
            return True
    return False


def end_stale_touches():
    """End touches whose frames stopped arriving (see STALE_TOUCH_SECONDS)."""
    now = time.monotonic()
    with state_lock:
        for g in gestures.values():
            if (g.touching or g.is_dragging) and now - g.last_frame_mono > STALE_TOUCH_SECONDS:
                log.warning(
                    "trackpad frames stopped mid-touch for %.1f s; ending the touch%s",
                    now - g.last_frame_mono, " and releasing the drag" if g.is_dragging else "",
                )
                g.reset()
                g.forget_taps()


def _watchdog():
    while True:
        time.sleep(0.2)
        try:
            end_stale_touches()
        except Exception:
            log.exception("error in touch watchdog")


class FinderKeys:
    """Windows-style keys in Finder: Return opens the selection (Cmd+Down),
    Delete/Backspace goes back (Cmd+[). Decided on key-down and remembered
    until the matching key-up, so an app never sees a mismatched up."""

    def __init__(self):
        self.app = None  # AXUIElement of Finder
        self.next_lookup = 0.0
        self.active = {}  # original keycode -> remapped keycode, while held
        self._strings = {}
        self.remap_now = False  # written by the poll thread, read by the tap
        self.stopped = False

    def start(self):
        threading.Thread(target=self._poll, name="finder-keys", daemon=True).start()

    def stop(self):
        self.stopped = True
        self.remap_now = False

    def _poll(self):
        while not self.stopped:
            try:
                self.remap_now = self.should_remap()
            except Exception:
                self.remap_now = False
            time.sleep(FINDER_POLL_SECONDS)

    def _cfstr(self, text):
        ref = self._strings.get(text)
        if ref is None:
            ref = cf.CFStringCreateWithCString(None, text.encode(), kCFStringEncodingUTF8)
            self._strings[text] = ref
        return ref

    def _attr(self, element, name):
        value = ctypes.c_void_p()
        err = appservices.AXUIElementCopyAttributeValue(element, self._cfstr(name), ctypes.byref(value))
        return err, value.value

    def _role(self, element):
        _, ref = self._attr(element, "AXRole")
        if not ref:
            return None
        role = None
        if cf.CFGetTypeID(ref) == cf.CFStringGetTypeID():
            buf = ctypes.create_string_buffer(128)
            if cf.CFStringGetCString(ref, buf, 128, kCFStringEncodingUTF8):
                role = buf.value.decode()
        cf.CFRelease(ref)
        return role

    def _finder(self):
        if self.app is not None:
            return self.app
        if time.time() < self.next_lookup:
            return None
        self.next_lookup = time.time() + 5
        try:
            pids = subprocess.run(
                ["/usr/bin/pgrep", "-x", "Finder"], capture_output=True, text=True, timeout=1
            ).stdout.split()
        except Exception:
            pids = []
        if not pids:
            return None
        self.app = appservices.AXUIElementCreateApplication(int(pids[0]))
        appservices.AXUIElementSetMessagingTimeout(self.app, 0.1)
        return self.app

    def should_remap(self):
        app = self._finder()
        if app is None:
            return False
        err, front = self._attr(app, "AXFrontmost")
        if err != 0:
            self.app = None  # Finder restarted or is not answering; re-resolve later
            return False
        is_front = bool(front) and cf.CFBooleanGetValue(front)
        if front:
            cf.CFRelease(front)
        if not is_front:
            return False
        err, focused = self._attr(app, "AXFocusedUIElement")
        if err != 0 or not focused:
            return False
        role = self._role(focused)
        cf.CFRelease(focused)
        if role not in FINDER_FILE_VIEW_ROLES:
            return False
        return True

    def handle(self, event_type, event):
        """Returns the event, remapped in place when appropriate. Runs inside
        the event tap: no I/O, no IPC, nothing that can wait."""
        keycode = cg.CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if event_type == kCGEventKeyUp:
            target = self.active.pop(keycode, None)
            if target is None:
                return event
        else:
            target = self.active.get(keycode)  # key repeat keeps the decision
            if target is None:
                if keycode not in FINDER_KEY_MAP:
                    return event
                if cg.CGEventGetFlags(event) & MODIFIER_MASK:
                    return event  # Cmd+Delete etc. untouched
                if not self.remap_now:
                    return event
                target = FINDER_KEY_MAP[keycode]
                self.active[keycode] = target
        cg.CGEventSetIntegerValueField(event, kCGKeyboardEventKeycode, target)
        cg.CGEventSetFlags(event, cg.CGEventGetFlags(event) | kCGEventFlagMaskCommand)
        return event


class PressGuard:
    """Event tap that swallows a real trackpad press (and its release) while a
    synthetic tap-drag is in progress. Measured on a real user: when dragging a
    window they often press the trackpad 80-150 ms after landing, i.e. after
    the synthetic drag has already started. Without this, macOS sees two
    button-downs and the real release ends the drag while the finger is still
    down. Events this script posts are tagged and always pass through."""

    def __init__(self):
        self.port = None
        self.loop = None
        self.swallow_up = False
        self.finder_keys = FinderKeys() if FINDER_WINDOWS_KEYS else None
        self.timeouts = []
        self.failed = False  # set by the tap thread, handled by the main loop
        self.retry_at = 0.0
        self.create_failures = 0
        self.ever_worked = False
        self._callback_ref = TAP_CALLBACK_TYPE(self._callback)  # keep alive

    def start(self):
        mask = (1 << kCGEventLeftMouseDown) | (1 << kCGEventLeftMouseUp)
        if self.finder_keys:
            mask |= (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp)
        self.port = cg.CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            mask,
            self._callback_ref,
            None,
        )
        if not self.port:
            self.create_failures += 1
            if self.create_failures == 1:
                log.warning("press guard unavailable (event tap could not be created)")
            return False
        self.create_failures = 0
        self.ever_worked = True
        self.failed = False
        self.timeouts = []
        threading.Thread(target=self._run, name="press-guard", daemon=True).start()
        if self.finder_keys:
            self.finder_keys.start()
        log.info("press guard active%s", " (+ Finder keys)" if self.finder_keys else "")
        return True

    def _run(self):
        boost_current_thread()
        self.loop = cf.CFRunLoopGetCurrent()
        source = cf.CFMachPortCreateRunLoopSource(None, self.port, 0)
        cf.CFRunLoopAddSource(cf.CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        cg.CGEventTapEnable(self.port, True)
        cf.CFRunLoopRun()

    def recover(self):
        """Called from the main loop after repeated tap timeouts: tear the tap
        down and come back with less (keys off first, then clicks paused)."""
        if self.loop:
            cf.CFRunLoopStop(self.loop)
            self.loop = None
        if self.port:
            cf.CFMachPortInvalidate(self.port)
            self.port = None
        if self.finder_keys:
            self.finder_keys.stop()
            self.finder_keys = None
            log.warning(
                "macOS reported the event tap as too slow; Finder keys are off "
                "until the script restarts so typing can't lag"
            )
        else:
            self.retry_at = time.time() + PRESS_GUARD_RETRY_SECONDS
            log.warning(
                "macOS reported the event tap as too slow; press guard paused "
                "for %.0f minutes", PRESS_GUARD_RETRY_SECONDS / 60
            )
        self.failed = False

    def _callback(self, proxy, event_type, event, refcon):
        try:
            if event_type == kCGEventTapDisabledByTimeout:
                # macOS turned the tap off because we answered too slowly.
                now = time.time()
                self.timeouts = [t for t in self.timeouts if now - t < TAP_TIMEOUT_WINDOW]
                self.timeouts.append(now)
                if len(self.timeouts) >= TAP_TIMEOUT_LIMIT:
                    self.failed = True  # leave it off; the main loop scales back
                else:
                    cg.CGEventTapEnable(self.port, True)
                return event
            if event_type == kCGEventTapDisabledByUserInput:
                cg.CGEventTapEnable(self.port, True)
                return event
            if event_type in (kCGEventKeyDown, kCGEventKeyUp):
                return self.finder_keys.handle(event_type, event) if self.finder_keys else event
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
    boost_current_thread()

    log.info("instant_release %s started (pid %d)", __version__, os.getpid())
    ensure_double_click_time()
    start_devices()
    threading.Thread(target=_watchdog, name="touch-watchdog", daemon=True).start()
    press_guard = PressGuard()
    if accessibility_trusted():
        press_guard.start()

    # time.time() keeps counting while the Mac sleeps, so a big jump between
    # one-second ticks means we just woke up and the device handles are stale.
    last_tick = time.time()
    last_mono = time.monotonic()
    while True:
        time.sleep(1)
        now = time.time()
        mono = time.monotonic()
        if (now - last_tick) > WAKE_GAP:
            # The monotonic clock pauses during sleep; if it advanced just as
            # much, the process was merely stalled by CPU load, not asleep.
            log.info(
                "wake from sleep detected (%.0fs gap, monotonic %.0fs), reattaching",
                now - last_tick,
                mono - last_mono,
            )
            time.sleep(1.5)
            start_devices()
        elif not active_devices:
            start_devices()
        if press_guard.failed:
            press_guard.recover()
        if (
            press_guard.port is None
            and time.time() >= press_guard.retry_at
            and accessibility_trusted()
        ):
            press_guard.start()
            if (
                press_guard.ever_worked
                and press_guard.create_failures >= TAP_RECREATE_FAILURES_BEFORE_RESTART
            ):
                log.warning(
                    "event tap can't be recreated after macOS turned it off; "
                    "restarting the script so clicks keep working"
                )
                with state_lock:
                    for g in gestures.values():
                        g.reset()
                stop_devices()
                sys.exit(75)  # launchd KeepAlive starts a fresh process
        last_tick = time.time()
        last_mono = time.monotonic()


if __name__ == "__main__":
    main()
