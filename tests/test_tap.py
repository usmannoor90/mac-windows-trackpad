#!/usr/bin/env python3
"""Event-tap tests for instant_release.py: Finder key remapping and the
slow-tap cutoff. Events are created but never posted. Run:
    python3 tests/test_tap.py"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instant_release as m  # noqa: E402

cg = m.cg
cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
failures = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def key(code, down=True, flags=0):
    ev = cg.CGEventCreateKeyboardEvent(None, code, down)
    if flags:
        cg.CGEventSetFlags(ev, flags)
    return ev


def code_of(ev):
    return cg.CGEventGetIntegerValueField(ev, m.kCGKeyboardEventKeycode)


def has_cmd(ev):
    return bool(cg.CGEventGetFlags(ev) & m.kCGEventFlagMaskCommand)


# The tap callback must never call into Finder or spawn anything.
fk = m.FinderKeys()
fk.should_remap = lambda: (_ for _ in ()).throw(AssertionError("IPC in tap"))
m.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess in tap"))

fk.remap_now = False
ev = fk.handle(m.kCGEventKeyDown, key(m.KEY_DELETE))
check("Delete outside Finder untouched", code_of(ev) == m.KEY_DELETE and not has_cmd(ev))

fk.remap_now = True
start = time.perf_counter()
ev = fk.handle(m.kCGEventKeyDown, key(m.KEY_RETURN))
check("Return in Finder file view -> Cmd+Down", code_of(ev) == m.KEY_DOWN_ARROW and has_cmd(ev))
fk.remap_now = False  # focus moved mid-press: the key-up must still match
ev = fk.handle(m.kCGEventKeyUp, key(m.KEY_RETURN, down=False))
check("matching key-up stays remapped", code_of(ev) == m.KEY_DOWN_ARROW and has_cmd(ev))
check("callback is instant (<5 ms)", time.perf_counter() - start < 0.005)

fk.remap_now = True
ev = fk.handle(m.kCGEventKeyDown, key(m.KEY_DELETE, flags=m.kCGEventFlagMaskCommand))
check("Cmd+Delete (Move to Trash) untouched", code_of(ev) == m.KEY_DELETE)
ev = fk.handle(m.kCGEventKeyDown, key(0))
check("letters untouched", code_of(ev) == 0 and not has_cmd(ev))

# Slow-tap cutoff: one timeout re-arms, a second within the window gives up.
pg = m.PressGuard()
pg.port = None
enabled = []
m.cg.CGEventTapEnable = lambda port, on: enabled.append(on)
pg._callback(None, m.kCGEventTapDisabledByTimeout, None, None)
check("first timeout re-enables the tap", enabled == [True] and not pg.failed)
pg._callback(None, m.kCGEventTapDisabledByTimeout, None, None)
check("second timeout leaves it off", enabled == [True] and pg.failed)
pg.recover()
check("recover turns Finder keys off first", pg.finder_keys is None and pg.port is None and not pg.failed)
pg.failed = True
pg.recover()
check("then pauses the press guard", pg.retry_at > time.time() + 60)

# Tap can't be recreated: counted once logged, so the main loop can restart.
pg2 = m.PressGuard()
pg2.finder_keys = None
real_create = m.cg.CGEventTapCreate
m.cg.CGEventTapCreate = lambda *a: None
pg2.ever_worked = True
for _ in range(m.TAP_RECREATE_FAILURES_BEFORE_RESTART):
    pg2.start()
check("failed recreations are counted for the restart",
      pg2.create_failures == m.TAP_RECREATE_FAILURES_BEFORE_RESTART and pg2.ever_worked)
m.cg.CGEventTapCreate = real_create

# Stuck-drag protection.
posted = []
m.post_mouse_event = lambda t, loc, **k: posted.append(t)
m.get_cursor_pos = lambda: m.CGPoint(0, 0)
g = m.Gestures()
m.gestures.clear()
m.gestures["dev"] = g
g.is_dragging = True
g.touching = True
g.last_frame_mono = time.monotonic()
check("press guard active during a live drag", m.any_drag_active())
g.last_frame_mono = time.monotonic() - 0.5
check("press guard lets clicks through once frames stop", not m.any_drag_active())


class NoLock:
    def __enter__(self):
        raise AssertionError("lock taken in tap path")

    def __exit__(self, *a):
        pass


real_lock = m.state_lock
m.state_lock = NoLock()
try:
    m.any_drag_active()
    check("press guard check takes no lock", True)
except AssertionError:
    check("press guard check takes no lock", False)
m.state_lock = real_lock

g.last_frame_mono = time.monotonic() - 0.5
m.end_stale_touches()
check("touch not ended while frames are only briefly late", g.is_dragging and not posted)
g.last_frame_mono = time.monotonic() - 2.0
m.end_stale_touches()
check("stale drag released (button up posted)", not g.is_dragging and not g.touching and posted == [m.kCGEventLeftMouseUp])

print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All tap tests passed.")
