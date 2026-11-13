// Gesture tests: fake fingers, fake clock, fake cursor. Prints PASS/FAIL per
// case and exits non-zero on any failure. Run with: swift run GestureTests
import CoreGraphics
import Foundation
import InstantReleaseCore

final class FakeEnvironment: GestureEnvironment {
    var time = 1000.0
    var cursor = CGPoint(x: 100, y: 100)
    var buttonDown = false
    var sinceKey = 99.0
    var events: [String] = []

    func now() -> Double { time }
    func cursorPosition() -> CGPoint { cursor }
    func physicalButtonDown() -> Bool { buttonDown }
    func secondsSinceKeyPress() -> Double { sinceKey }
    func log(_ message: String) {}
    func post(_ action: MouseAction) {
        switch action {
        case .leftDown(_, let c): events.append("Ldown\(c)")
        case .leftUp(_, let c): events.append("Lup\(c)")
        case .rightDown: events.append("Rdown")
        case .rightUp: events.append("Rup")
        case .drag: if events.last != "drag" { events.append("drag") }
        }
    }
}

struct Sim {
    let env = FakeEnvironment()
    let engine: GestureEngine
    init() { engine = GestureEngine(environment: env) }

    /// One touch: `n` fingers for `duration` seconds. `hold` is a still phase
    /// before any movement. `move` is cursor travel in px, `travel` is finger
    /// travel on the glass (fraction of pad), both spread over the duration.
    func touch(fingers n: Int, duration: Double, move: CGPoint = .zero, travel: Float = 0, hold: Double = 0) {
        let still = (0..<n).map { FingerSample(id: Int32($0), x: 0.5, y: 0.5) }
        let holdEnd = env.time + hold
        while env.time < holdEnd {
            engine.onFrame(still)
            env.time += 0.01
        }
        let steps = max(Int(duration / 0.01), 1)
        for s in 0...steps {
            let frac = Float(s) / Float(steps)
            let fingers = (0..<n).map { FingerSample(id: Int32($0), x: 0.5 + travel * frac, y: 0.5) }
            if s > 0 {
                env.cursor.x += move.x / CGFloat(steps)
                env.cursor.y += move.y / CGFloat(steps)
            }
            engine.onFrame(fingers)
            env.time += 0.01
        }
        engine.onFrame([])
    }

    func gap(_ s: Double) { env.time += s }
}

var failures = 0
func check(_ name: String, _ expect: [String], _ body: (Sim) -> Void) {
    let sim = Sim()
    body(sim)
    let ok = sim.env.events == expect
    print((ok ? "PASS" : "FAIL") + " " + name + (ok ? "" : "  got \(sim.env.events)"))
    if !ok { failures += 1 }
}

let tap = ["Ldown1", "Lup1"]
let dragSeq = ["Ldown1", "drag", "Lup1"]

check("single tap", tap) { $0.touch(fingers: 1, duration: 0.08) }

check("double tap", tap + ["Ldown2", "Lup2"]) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1); $0.touch(fingers: 1, duration: 0.08)
}

check("triple tap", tap + ["Ldown2", "Lup2", "Ldown3", "Lup3"]) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1)
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1)
    $0.touch(fingers: 1, duration: 0.08)
}

check("tap, rest, then drag", tap + dragSeq) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1)
    $0.touch(fingers: 1, duration: 0.4, move: CGPoint(x: 80, y: 0), travel: 0.2, hold: 0.16)
}

check("slow taps = two singles", tap + tap) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.6); $0.touch(fingers: 1, duration: 0.08)
}

check("cursor move = nothing", []) {
    $0.touch(fingers: 1, duration: 0.3, move: CGPoint(x: 200, y: 0), travel: 0.3)
}

check("two-finger tap = right click", ["Rdown", "Rup"]) { $0.touch(fingers: 2, duration: 0.08) }

check("two-finger scroll = nothing", []) { $0.touch(fingers: 2, duration: 0.4, travel: 0.2) }

check("tap right after scroll ignored", []) {
    $0.touch(fingers: 2, duration: 0.4, travel: 0.2); $0.gap(0.1); $0.touch(fingers: 1, duration: 0.08)
}

check("physical click not doubled", []) {
    $0.env.buttonDown = true; $0.touch(fingers: 1, duration: 0.1)
}

check("palm tap while typing ignored", []) {
    $0.env.sinceKey = 0.1; $0.touch(fingers: 1, duration: 0.08)
}

check("tap a tab then move fast = no drag", tap) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.05)
    $0.touch(fingers: 1, duration: 0.4, move: CGPoint(x: 150, y: 40), travel: 0.3)
}

check("double-tap-drag, slides after 70 ms", tap + dragSeq) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.12)
    $0.touch(fingers: 1, duration: 0.4, move: CGPoint(x: 80, y: 0), travel: 0.2, hold: 0.07)
}

check("double-tap-drag, slow slide right away", tap + dragSeq) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.12)
    $0.touch(fingers: 1, duration: 0.8, move: CGPoint(x: 40, y: 0), travel: 0.08)
}

check("tap a tab, moderate move right away = no drag", tap) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1)
    $0.touch(fingers: 1, duration: 0.3, move: CGPoint(x: 60, y: 0), travel: 0.1)
}

check("no drag after double-click", tap + ["Ldown2", "Lup2"]) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1)
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1)
    $0.touch(fingers: 1, duration: 0.4, move: CGPoint(x: 80, y: 0), travel: 0.2)
}

check("drag interrupted by sleep releases button", tap + dragSeq) { sim in
    sim.touch(fingers: 1, duration: 0.08); sim.gap(0.1)
    let finger = [FingerSample(id: 0, x: 0.5, y: 0.5)]
    for _ in 0..<14 { sim.engine.onFrame(finger); sim.env.time += 0.01 }
    for _ in 0..<30 {
        sim.env.cursor.x += 3
        sim.engine.onFrame(finger)
        sim.env.time += 0.01
    }
    sim.engine.reset()
}

check("slow second tap (150 ms) = double-click", tap + ["Ldown2", "Lup2"]) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1); $0.touch(fingers: 1, duration: 0.15)
}

check("tap, then touch and hold still = press", tap + dragSeq) {
    $0.touch(fingers: 1, duration: 0.08); $0.gap(0.1); $0.touch(fingers: 1, duration: 0.4)
}

if failures == 0 {
    print("All gesture tests passed.")
    exit(0)
} else {
    print("\(failures) gesture test(s) FAILED.")
    exit(1)
}
