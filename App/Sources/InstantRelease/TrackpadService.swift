import AppKit
import CMultitouch
import InstantReleaseCore
import os.log

let logger = Logger(subsystem: "com.usmannoor90.InstantRelease", category: "gestures")

/// Posts real mouse events and reads real input state.
final class SystemEnvironment: GestureEnvironment {
    private let modifierMask: CGEventFlags = [.maskShift, .maskControl, .maskAlternate, .maskCommand]

    func now() -> Double { Date().timeIntervalSince1970 }

    func cursorPosition() -> CGPoint { CGEvent(source: nil)?.location ?? .zero }

    func physicalButtonDown() -> Bool {
        CGEventSource.buttonState(.hidSystemState, button: .left)
            || CGEventSource.buttonState(.hidSystemState, button: .right)
    }

    func secondsSinceKeyPress() -> Double {
        CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: .keyDown)
    }

    func log(_ message: String) { logger.debug("\(message, privacy: .public)") }

    func post(_ action: MouseAction) {
        let event: CGEvent?
        switch action {
        case .leftDown(let p, let clicks):
            event = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
            event?.setIntegerValueField(.mouseEventClickState, value: Int64(clicks))
        case .leftUp(let p, let clicks):
            event = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)
            event?.setIntegerValueField(.mouseEventClickState, value: Int64(clicks))
        case .rightDown(let p):
            event = CGEvent(mouseEventSource: nil, mouseType: .rightMouseDown, mouseCursorPosition: p, mouseButton: .right)
            event?.setIntegerValueField(.mouseEventClickState, value: 1)
        case .rightUp(let p):
            event = CGEvent(mouseEventSource: nil, mouseType: .rightMouseUp, mouseCursorPosition: p, mouseButton: .right)
            event?.setIntegerValueField(.mouseEventClickState, value: 1)
        case .drag(let p, let delta):
            event = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDragged, mouseCursorPosition: p, mouseButton: .left)
            event?.setIntegerValueField(.mouseEventClickState, value: 1)
            event?.setIntegerValueField(.mouseEventDeltaX, value: Int64(delta.x.rounded()))
            event?.setIntegerValueField(.mouseEventDeltaY, value: Int64(delta.y.rounded()))
        }
        guard let event else { return }
        // Carry held modifier keys so Cmd/Shift/Option/Ctrl + tap work like a click.
        event.flags = CGEventSource.flagsState(.hidSystemState).intersection(modifierMask)
        event.post(tap: .cghidEventTap)
    }
}

struct TrackpadInfo {
    let family: Int32
    let builtIn: Bool
    var isMouse: Bool { TrackpadService.mouseFamilies.contains(family) }
    var name: String {
        if isMouse { return "Magic Mouse (ignored)" }
        return builtIn ? "Built-in trackpad" : "External trackpad"
    }
}

/// Attaches to every trackpad and feeds frames to one GestureEngine per device.
final class TrackpadService {
    static let shared = TrackpadService()
    /// Multitouch devices that are mice, not trackpads. Touching a Magic Mouse
    /// must never produce clicks.
    static let mouseFamilies: Set<Int32> = [112, 113]

    private let lock = NSLock()
    private var engines: [UnsafeMutableRawPointer: GestureEngine] = [:]
    private var devices: [UnsafeMutableRawPointer] = []
    private let environment = SystemEnvironment()
    private(set) var attached: [TrackpadInfo] = []
    private(set) var frameworkAvailable = MTLoad()

    var isRunning: Bool { !devices.isEmpty }

    /// Lists devices without attaching.
    func scan() -> [TrackpadInfo] {
        guard frameworkAvailable, let list = MTCreateDeviceList() else { return [] }
        return (0..<CFArrayGetCount(list)).compactMap { i in
            guard let raw = CFArrayGetValueAtIndex(list, i) else { return nil }
            let dev = UnsafeMutableRawPointer(mutating: raw)
            return TrackpadInfo(family: MTFamilyID(dev), builtIn: MTIsBuiltIn(dev))
        }
    }

    @discardableResult
    func start() -> Bool {
        stop()
        guard frameworkAvailable, let list = MTCreateDeviceList() else {
            logger.error("MultitouchSupport framework unavailable")
            return false
        }
        var infos: [TrackpadInfo] = []
        for i in 0..<CFArrayGetCount(list) {
            guard let raw = CFArrayGetValueAtIndex(list, i) else { continue }
            let dev = UnsafeMutableRawPointer(mutating: raw)
            let info = TrackpadInfo(family: MTFamilyID(dev), builtIn: MTIsBuiltIn(dev))
            infos.append(info)
            if info.isMouse {
                logger.info("skipping mouse (family \(info.family))")
                continue
            }
            lock.lock()
            engines[dev] = GestureEngine(environment: environment)
            lock.unlock()
            MTRegisterCallback(dev, contactCallback)
            MTStart(dev)
            devices.append(dev)
            logger.info("attached to \(info.name, privacy: .public) (family \(info.family))")
        }
        attached = infos
        return !devices.isEmpty
    }

    func stop() {
        for dev in devices {
            MTUnregisterCallback(dev, contactCallback)
            MTStop(dev)
        }
        devices = []
        lock.lock()
        engines.values.forEach { $0.reset() }
        engines = [:]
        lock.unlock()
    }

    /// Called from the multitouch thread for every frame.
    fileprivate func handleFrame(device: UnsafeMutableRawPointer, fingers: UnsafeMutablePointer<MTFinger>?, count: Int) {
        var samples: [FingerSample] = []
        if let fingers, count > 0 {
            samples.reserveCapacity(count)
            for i in 0..<count {
                let f = fingers[i]
                samples.append(FingerSample(id: f.identifier, x: f.normalized.position.x, y: f.normalized.position.y))
            }
        }
        lock.lock()
        let engine = engines[device]
        lock.unlock()
        engine?.onFrame(samples)
    }
}

// A C callback cannot capture context, so it goes through the singleton.
private let contactCallback: MTContactCallback = { device, fingers, count, _, _ in
    guard let device else { return 0 }
    TrackpadService.shared.handleFrame(device: device, fingers: fingers, count: Int(count))
    return 0
}
