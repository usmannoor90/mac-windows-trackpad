import CoreGraphics
import Foundation

/// All timing and distance thresholds. Seconds, screen points, or fraction of
/// the trackpad surface. Same values as instant_release.py v1.4.0.
public enum Tuning {
    public static let tapMaxDuration = 0.22
    public static let tapMaxCursorMove = 6.0
    public static let tapMaxFingerTravel: Float = 0.025
    public static let doubleTapMaxGap = 0.38
    public static let doubleTapMaxShift = 10.0
    public static let dragArmWindow = 0.30
    /// After a tap, the next touch is judged during its first `dragLandingTime`:
    /// a finger that lands still (a double-tap motion) becomes a drag; a finger
    /// that is already sliding when it lands is just moving the cursor.
    public static let dragLandingTime = 0.06
    public static let dragLandingTravel: Float = 0.012
    public static let dragStartMove = 3.0
    public static let multitouchCooldown = 0.35
    public static let rightTapMaxDuration = 0.18
    public static let rightTapMaxFingerTravel: Float = 0.015
    public static let typingCooldown = 0.30
}

/// A mouse event the engine wants posted.
public enum MouseAction: Equatable {
    case leftDown(CGPoint, clicks: Int)
    case leftUp(CGPoint, clicks: Int)
    case rightDown(CGPoint)
    case rightUp(CGPoint)
    case drag(CGPoint, delta: CGPoint)
}

/// Everything the engine needs from the outside world, so tests can fake it.
public protocol GestureEnvironment: AnyObject {
    func now() -> Double
    func cursorPosition() -> CGPoint
    func physicalButtonDown() -> Bool
    func secondsSinceKeyPress() -> Double
    func post(_ action: MouseAction)
    func log(_ message: String)
}

/// One finger on the glass, in normalized 0...1 coordinates.
public struct FingerSample {
    public let id: Int32
    public let x: Float
    public let y: Float
    public init(id: Int32, x: Float, y: Float) {
        self.id = id
        self.x = x
        self.y = y
    }
}

/// Gesture state machine for one trackpad. Call `onFrame` for every frame the
/// hardware delivers (including the empty frame when the last finger lifts).
public final class GestureEngine {
    private let env: GestureEnvironment

    // Persists across touches.
    private var lastTapUpTime = 0.0
    private var lastTapPos: CGPoint?
    private var clickCount = 0
    private var lastMultitouchTime = 0.0
    public private(set) var isDragging = false
    private var lastDragPos = CGPoint.zero

    // Per-touch.
    private var touching = false
    private var maxFingers = 0
    private var touchStartTime = 0.0
    private var touchStartPos: CGPoint?
    private var dragStartPos: CGPoint?
    private var dragReady = false
    private var fingerStart: [Int32: (Float, Float)] = [:]
    private var maxTravel: Float = 0
    private var physicalClick = false
    private var typing = false

    public init(environment: GestureEnvironment) {
        env = environment
    }

    private func resetTouch() {
        touching = false
        maxFingers = 0
        touchStartTime = 0
        touchStartPos = nil
        dragStartPos = nil
        dragReady = false
        fingerStart = [:]
        maxTravel = 0
        physicalClick = false
        typing = false
    }

    private func forgetTaps() {
        lastTapUpTime = 0
        lastTapPos = nil
        clickCount = 0
    }

    /// Never leave the left button stuck down.
    public func releaseDrag() {
        if isDragging {
            env.post(.leftUp(env.cursorPosition(), clicks: 1))
            isDragging = false
            env.log("drag released")
        }
    }

    /// Cancel whatever is in progress (sleep, shutdown, device loss).
    public func reset() {
        releaseDrag()
        resetTouch()
    }

    public func onFrame(_ fingers: [FingerSample]) {
        let now = env.now()
        if !fingers.isEmpty {
            trackTravel(fingers)
            if !touching {
                touchBegan(fingers.count, now)
            } else {
                touchMoved(fingers.count, now)
            }
            // A real press is handled entirely by macOS; we must not add a
            // second click (that would toggle checkboxes twice, etc.).
            if !isDragging, !physicalClick, env.physicalButtonDown() {
                physicalClick = true
                dragStartPos = nil
                env.log("physical click, leaving it to macOS")
            }
        } else if touching {
            touchEnded(now)
        }
    }

    private func trackTravel(_ fingers: [FingerSample]) {
        for f in fingers {
            if let start = fingerStart[f.id] {
                let dx = f.x - start.0, dy = f.y - start.1
                maxTravel = max(maxTravel, (dx * dx + dy * dy).squareRoot())
            } else {
                fingerStart[f.id] = (f.x, f.y)
            }
        }
    }

    private func touchBegan(_ n: Int, _ now: Double) {
        touching = true
        maxFingers = n
        touchStartTime = now
        touchStartPos = env.cursorPosition()
        typing = env.secondsSinceKeyPress() < Tuning.typingCooldown
        if n == 1,
           clickCount == 1,  // no drag right after a double-click
           !typing,
           now - lastTapUpTime < Tuning.dragArmWindow,
           now - lastMultitouchTime > Tuning.multitouchCooldown {
            dragStartPos = touchStartPos
        }
    }

    private func touchMoved(_ n: Int, _ now: Double) {
        maxFingers = max(maxFingers, n)

        if n > 1 {
            // Scroll / multi-finger gesture: cancel any tap-drag in progress.
            lastMultitouchTime = now
            releaseDrag()
            dragStartPos = nil
            forgetTaps()
        } else if maxFingers == 1 {
            if let start = dragStartPos, !isDragging {
                let elapsed = now - touchStartTime
                if !dragReady {
                    if maxTravel > Tuning.dragLandingTravel {
                        // Already sliding as it landed: cursor move, not a drag.
                        dragStartPos = nil
                        env.log(String(format: "no drag: sliding on landing (travel %.3f in %.0f ms)",
                                       maxTravel, elapsed * 1000))
                        return
                    } else if elapsed >= Tuning.dragLandingTime {
                        dragReady = true
                    }
                }
                // Landed still. Press once the finger actually moves, or is
                // held longer than a tap; lifting before that is a double-tap.
                if dragReady,
                   distance(env.cursorPosition(), start) > Tuning.dragStartMove
                   || elapsed > Tuning.tapMaxDuration {
                    isDragging = true
                    lastDragPos = start
                    env.post(.leftDown(start, clicks: 1))
                    env.log(String(format: "drag started (%.0f ms after landing)", elapsed * 1000))
                }
            }
            if isDragging {
                let cur = env.cursorPosition()
                let delta = CGPoint(x: cur.x - lastDragPos.x, y: cur.y - lastDragPos.y)
                lastDragPos = cur
                env.post(.drag(cur, delta: delta))
            }
        }
    }

    private func touchEnded(_ now: Double) {
        let cur = env.cursorPosition()
        let duration = now - touchStartTime
        let cursorMove = touchStartPos.map { distance(cur, $0) } ?? 0

        if isDragging {
            // The whole point: release the instant the last finger lifts.
            env.post(.leftUp(cur, clicks: 1))
            isDragging = false
            forgetTaps()
            env.log("drag ended")

        } else if physicalClick || typing {
            forgetTaps()
            if typing { env.log("touch ignored: typing") }

        } else if maxFingers == 1,
                  duration < Tuning.tapMaxDuration,
                  cursorMove < Tuning.tapMaxCursorMove,
                  maxTravel < Tuning.tapMaxFingerTravel,
                  now - lastMultitouchTime > Tuning.multitouchCooldown {
            let gap = now - lastTapUpTime
            let shift = lastTapPos.map { distance(cur, $0) } ?? 999
            let pos: CGPoint
            if gap < Tuning.doubleTapMaxGap, shift < Tuning.doubleTapMaxShift, let last = lastTapPos {
                clickCount += 1  // double, triple, ... at the same spot
                pos = last
            } else {
                clickCount = 1
                pos = cur
            }
            env.post(.leftDown(pos, clicks: clickCount))
            env.post(.leftUp(pos, clicks: clickCount))
            lastTapUpTime = now
            lastTapPos = pos
            env.log(String(format: "tap x%d (%.0f ms)", clickCount, duration * 1000))

        } else if maxFingers == 2,
                  duration < Tuning.rightTapMaxDuration,
                  maxTravel < Tuning.rightTapMaxFingerTravel {
            env.post(.rightDown(cur))
            env.post(.rightUp(cur))
            lastMultitouchTime = now
            forgetTaps()
            env.log("right click")

        } else {
            if maxFingers > 1 { lastMultitouchTime = now }
            forgetTaps()
        }

        resetTouch()
    }

    private func distance(_ a: CGPoint, _ b: CGPoint) -> Double {
        let dx = a.x - b.x, dy = a.y - b.y
        return (dx * dx + dy * dy).squareRoot()
    }
}
