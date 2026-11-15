#!/usr/bin/env python3
"""Gesture tests for instant_release.py: fake fingers, fake clock, fake cursor.
Nothing is posted to the system. Run:  python3 tests/test_gestures.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instant_release as m  # noqa: E402

T = [1000.0]
CUR = [m.CGPoint(100, 100)]
BTN = [False]
KEY = [99.0]
EV = []
NAMES = {1: "Ldown", 2: "Lup", 3: "Rdown", 4: "Rup", 6: "drag"}

m.time.time = lambda: T[0]
m.get_cursor_pos = lambda: m.CGPoint(CUR[0].x, CUR[0].y)
m.physical_button_down = lambda: BTN[0]
m.seconds_since_keypress = lambda: KEY[0]


def fake_post(event_type, loc, button=0, click_state=1, delta=None):
    if event_type == 6:
        if EV and EV[-1] == "drag":
            return
        EV.append("drag")
    else:
        EV.append((NAMES[event_type], click_state))


m.post_mouse_event = fake_post


def frames(g, n, dur, move=(0, 0), travel=0.0, hold=0.0, now=None):
    """One touch of `n` fingers lasting `dur` s. `hold` = still phase before any
    movement; `move` = cursor px, `travel` = finger travel on the glass
    (fraction of pad), both spread over `dur`. `now` = callable giving the
    timestamp to pass explicitly (hardware clock), else the fake time.time."""
    arr = (m.MTFinger * 3)()
    for i in range(n):
        arr[i].identifier = i
        arr[i].normalized.position.x = 0.5
        arr[i].normalized.position.y = 0.5
    ts = (lambda: None) if now is None else now
    hold_end = T[0] + hold
    while T[0] < hold_end:
        g.on_frame(arr, n, ts())
        T[0] += 0.01
    steps = max(int(dur / 0.01), 1)
    for s in range(steps + 1):
        for i in range(n):
            arr[i].normalized.position.x = 0.5 + travel * s / steps
        if s:
            CUR[0] = m.CGPoint(CUR[0].x + move[0] / steps, CUR[0].y + move[1] / steps)
        g.on_frame(arr, n, ts())
        T[0] += 0.01
    g.on_frame(arr, 0, ts())


def gap(s):
    T[0] += s


failures = []


def case(name, fn, expect):
    del EV[:]
    T[0] += 5
    BTN[0] = False
    KEY[0] = 99.0
    g = m.Gestures()
    fn(g)
    ok = EV == expect
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   got %r" % (EV,)))
    if not ok:
        failures.append(name)


TAP = [("Ldown", 1), ("Lup", 1)]
DRAG = [("Ldown", 1), "drag", ("Lup", 1)]

case("single tap", lambda g: frames(g, 1, 0.08), TAP)
case("double tap", lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.08)),
     TAP + [("Ldown", 2), ("Lup", 2)])
case("triple tap", lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.08)),
     TAP + [("Ldown", 2), ("Lup", 2), ("Ldown", 3), ("Lup", 3)])
case("tap, rest, then drag", lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.4, move=(80, 0), travel=0.2, hold=0.16)),
     TAP + DRAG)
case("slow taps = two singles", lambda g: (frames(g, 1, 0.08), gap(0.6), frames(g, 1, 0.08)), TAP + TAP)
case("cursor move = nothing", lambda g: frames(g, 1, 0.3, move=(200, 0), travel=0.3), [])
case("two-finger tap = right click", lambda g: frames(g, 2, 0.08), [("Rdown", 1), ("Rup", 1)])
case("two-finger scroll = nothing", lambda g: frames(g, 2, 0.4, travel=0.2), [])
case("tap right after scroll ignored", lambda g: (frames(g, 2, 0.4, travel=0.2), gap(0.1), frames(g, 1, 0.08)), [])


def physical(g):
    BTN[0] = True
    frames(g, 1, 0.1)


case("physical click not doubled", physical, [])


def typing(g):
    KEY[0] = 0.1
    frames(g, 1, 0.08)


case("palm tap while typing ignored", typing, [])
case("tap piece, 0.20 s later move cursor = no drag",
     lambda g: (frames(g, 1, 0.08), gap(0.20), frames(g, 1, 0.4, move=(150, 40), travel=0.3)), TAP)
case("tap, 0.17 s later move (measured min) = no drag",
     lambda g: (frames(g, 1, 0.08), gap(0.17), frames(g, 1, 0.4, move=(150, 40), travel=0.3)), TAP)
case("double-tap-drag, 0.11 s gap (measured max) = drag",
     lambda g: (frames(g, 1, 0.08), gap(0.11), frames(g, 1, 0.4, move=(80, 0), travel=0.2)), TAP + DRAG)
case("double-tap-drag, slides after 70 ms",
     lambda g: (frames(g, 1, 0.08), gap(0.12), frames(g, 1, 0.4, move=(80, 0), travel=0.2, hold=0.07)), TAP + DRAG)
case("double-tap-drag, slow slide right away",
     lambda g: (frames(g, 1, 0.08), gap(0.12), frames(g, 1, 0.8, move=(40, 0), travel=0.08)), TAP + DRAG)
case("double-tap-drag, sliding as it lands = drag",
     lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.3, move=(60, 0), travel=0.1)), TAP + DRAG)
case("no drag after double-click",
     lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.4, move=(80, 0), travel=0.2)),
     TAP + [("Ldown", 2), ("Lup", 2)])


def interrupted(g):
    frames(g, 1, 0.08)
    gap(0.1)
    arr = (m.MTFinger * 3)()
    arr[0].identifier = 0
    for _ in range(14):
        g.on_frame(arr, 1)
        T[0] += 0.01
    for _ in range(30):
        CUR[0] = m.CGPoint(CUR[0].x + 3, CUR[0].y)
        g.on_frame(arr, 1)
        T[0] += 0.01
    g.reset()


case("drag interrupted by sleep releases button", interrupted, TAP + DRAG)
case("slow second tap (150 ms) = double-click",
     lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.15)), TAP + [("Ldown", 2), ("Lup", 2)])
case("tap, then touch and hold still = press",
     lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.4)), TAP + DRAG)


def late_processing(g):
    # Hardware timestamps say 80 ms; the wall clock (processing time) is
    # wildly off. The tap must still be a tap.
    hw = [5000.0]

    def ts():
        hw[0] += 0.01
        return hw[0]

    T[0] += 123.0
    frames(g, 1, 0.08, now=ts)


case("hardware timestamps beat a stalled wall clock", late_processing, TAP)


def raw(g, seq):
    """Feed explicit frames: seq is a list of lists of (id, x, y) per frame."""
    for contacts in seq:
        arr = (m.MTFinger * 3)()
        for i, (fid, x, y) in enumerate(contacts):
            arr[i].identifier = fid
            arr[i].normalized.position.x = x
            arr[i].normalized.position.y = y
        g.on_frame(arr, len(contacts))
        T[0] += 0.008


ONE = [(0, 0.5, 0.5)]
PHANTOM = [(0, 0.5, 0.5), (9, 0.52, 0.5)]
TWO = [(0, 0.4, 0.5), (1, 0.6, 0.5)]
TWO_SWAPPED = [(1, 0.4, 0.5), (0, 0.6, 0.5)]


def phantom_tap(g):
    raw(g, [PHANTOM] + [ONE] * 8 + [[]])


case("phantom 2nd contact for one frame = still a tap", phantom_tap, TAP)


def phantom_then_double(g):
    phantom_tap(g)
    gap(0.1)
    raw(g, [PHANTOM, PHANTOM] + [ONE] * 7 + [[]])


case("double-tap where both taps have phantom contacts", phantom_then_double, TAP + [("Ldown", 2), ("Lup", 2)])
case("sloppy 2nd tap (6% slide, cursor still) = double-click",
     lambda g: (frames(g, 1, 0.08), gap(0.1), frames(g, 1, 0.07, travel=0.06)), TAP + [("Ldown", 2), ("Lup", 2)])
case("sloppy FIRST tap (6% slide) = not a tap", lambda g: frames(g, 1, 0.07, travel=0.06), [])
case("two-finger tap lasting 0.22 s = right click", lambda g: raw(g, [TWO] * 27 + [[]]), [("Rdown", 1), ("Rup", 1)])
case("two-finger tap with finger-ID swap = right click",
     lambda g: raw(g, [TWO] * 5 + [TWO_SWAPPED] * 5 + [[]]), [("Rdown", 1), ("Rup", 1)])


def two_finger_flick(g):
    seq = [[(0, 0.4 + 0.15 * i / 20, 0.5), (1, 0.6 + 0.15 * i / 20, 0.5)] for i in range(21)]
    raw(g, seq + [[]])


case("two-finger flick (midpoint moves 15%) = nothing", two_finger_flick, [])


def blip_during_drag(g):
    frames(g, 1, 0.08)
    gap(0.1)
    arr = [ONE] * 14
    raw(g, arr)
    for i in range(30):
        CUR[0] = m.CGPoint(CUR[0].x + 3, CUR[0].y)
        raw(g, [PHANTOM if i == 10 else ONE])
    raw(g, [[]])


case("phantom contact during a drag does not end it", blip_during_drag, TAP + DRAG)

print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All %d gesture tests passed." % 30)
