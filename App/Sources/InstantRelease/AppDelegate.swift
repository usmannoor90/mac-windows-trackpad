import AppKit
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private let service = TrackpadService.shared
    private var watchdog: Timer?
    private var lastTick = Date()
    private var permissionPoll: Timer?

    private var enabled: Bool {
        get { UserDefaults.standard.object(forKey: "enabled") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "enabled") }
    }

    private var version: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"
    }

    // MARK: - Lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "hand.tap", accessibilityDescription: "Instant Release")
        statusItem.button?.image?.isTemplate = true
        menu.delegate = self
        statusItem.menu = menu

        if !service.frameworkAvailable {
            alert("This version of macOS doesn't expose the trackpad framework Instant Release needs.",
                  "The app can't work on this Mac. Sorry!")
            NSApp.terminate(nil)
            return
        }

        requestAccessibility()

        // Sleep/wake: the trackpad handle goes stale, so reattach after wake.
        let nc = NSWorkspace.shared.notificationCenter
        nc.addObserver(self, selector: #selector(didWake), name: NSWorkspace.didWakeNotification, object: nil)
        nc.addObserver(self, selector: #selector(didWake), name: NSWorkspace.screensDidWakeNotification, object: nil)

        // Fallback: a clock jump between ticks also means we slept.
        watchdog = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            let now = Date()
            if now.timeIntervalSince(self.lastTick) > 3 {
                logger.info("clock jump detected, reattaching")
                self.restart(after: 1.5)
            } else if self.enabled, !self.service.isRunning, AXIsProcessTrusted() {
                self.service.start()
                self.refreshIcon()
            }
            self.lastTick = Date()
        }

        startIfReady()

        // Ask after everything is running, so the dialog never blocks startup.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.offerToRemoveLegacyScript()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        service.stop()
    }

    @objc private func didWake() { restart(after: 1.5) }

    private func restart(after delay: Double) {
        service.stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.startIfReady()
        }
    }

    private func startIfReady() {
        if enabled, AXIsProcessTrusted() {
            service.start()
        } else {
            service.stop()
        }
        refreshIcon()
    }

    private func refreshIcon() {
        let active = enabled && service.isRunning && AXIsProcessTrusted()
        statusItem.button?.appearsDisabled = !active
    }

    // MARK: - Permissions

    private func requestAccessibility() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary
        if AXIsProcessTrustedWithOptions(options) { return }
        // Start automatically the moment the user flips the switch.
        permissionPoll = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] t in
            guard let self, AXIsProcessTrusted() else { return }
            t.invalidate()
            self.permissionPoll = nil
            logger.info("accessibility granted")
            self.startIfReady()
        }
    }

    // MARK: - Native trackpad settings that conflict with the app

    private func nativeSettingOn(_ key: String) -> Bool {
        let domains = ["com.apple.AppleMultitouchTrackpad", "com.apple.driver.AppleBluetoothMultitouch.trackpad"]
        for domain in domains {
            if let value = CFPreferencesCopyAppValue(key as CFString, domain as CFString) as? NSNumber, value.intValue != 0 {
                return true
            }
        }
        return false
    }

    // MARK: - Legacy Python script (v1.x) installed by install.sh

    private var legacyPlist: String { NSHomeDirectory() + "/Library/LaunchAgents/com.user.instantrelease.plist" }
    private var legacyInstalled: Bool { FileManager.default.fileExists(atPath: legacyPlist) }

    private func offerToRemoveLegacyScript() {
        guard legacyInstalled else { return }
        let a = NSAlert()
        a.messageText = "Remove the old script version?"
        a.informativeText = "The Python script from an earlier version of Instant Release is still set to run at login. Running both at once would double every click. The app replaces it completely."
        a.addButton(withTitle: "Remove Old Script")
        a.addButton(withTitle: "Later")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn { removeLegacyScript() }
    }

    @objc private func removeLegacyScript() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["bootout", "gui/\(getuid())/com.user.instantrelease"]
        try? p.run()
        p.waitUntilExit()
        let fm = FileManager.default
        for path in [legacyPlist,
                     NSHomeDirectory() + "/.local/bin/instant_release.py",
                     NSHomeDirectory() + "/.local/bin/instant_release.py.bak"] {
            try? fm.removeItem(atPath: path)
        }
        logger.info("legacy script removed")
    }

    // MARK: - Menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let trusted = AXIsProcessTrusted()

        let status: String
        if !trusted { status = "Needs Accessibility permission" }
        else if !enabled { status = "Paused" }
        else if service.isRunning { status = "Running" }
        else { status = "No trackpad found" }
        let title = menu.addItem(withTitle: "Instant Release \(version) — \(status)", action: nil, keyEquivalent: "")
        title.isEnabled = false

        for info in service.attached where !info.isMouse {
            let item = menu.addItem(withTitle: "    \(info.name)", action: nil, keyEquivalent: "")
            item.isEnabled = false
        }
        menu.addItem(.separator())

        let toggle = menu.addItem(withTitle: enabled ? "Pause" : "Resume", action: #selector(toggleEnabled), keyEquivalent: "")
        toggle.target = self

        let login = menu.addItem(withTitle: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off

        if !trusted {
            menu.addItem(.separator())
            let fix = menu.addItem(withTitle: "Allow Accessibility Access…", action: #selector(openAccessibilitySettings), keyEquivalent: "")
            fix.target = self
        }

        if nativeSettingOn("Clicking") || nativeSettingOn("Dragging") {
            menu.addItem(.separator())
            let warn = menu.addItem(withTitle: "⚠️ Conflicting macOS settings", action: nil, keyEquivalent: "")
            warn.isEnabled = false
            if nativeSettingOn("Clicking") {
                let i = menu.addItem(withTitle: "Turn off “Tap to click” in Trackpad settings…", action: #selector(openTrackpadSettings), keyEquivalent: "")
                i.target = self
            }
            if nativeSettingOn("Dragging") {
                let i = menu.addItem(withTitle: "Turn off “Use trackpad for dragging”…", action: #selector(openPointerSettings), keyEquivalent: "")
                i.target = self
            }
        }

        if legacyInstalled {
            menu.addItem(.separator())
            let i = menu.addItem(withTitle: "Remove Old Script Version", action: #selector(removeLegacyScript), keyEquivalent: "")
            i.target = self
        }

        menu.addItem(.separator())
        let help = menu.addItem(withTitle: "Help & Updates (GitHub)", action: #selector(openGitHub), keyEquivalent: "")
        help.target = self
        let quit = menu.addItem(withTitle: "Quit Instant Release", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
    }

    @objc private func toggleEnabled() {
        enabled.toggle()
        startIfReady()
    }

    @objc private func toggleLaunchAtLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            alert("Couldn't change Launch at Login", error.localizedDescription)
        }
    }

    @objc private func openAccessibilitySettings() {
        open("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }

    @objc private func openTrackpadSettings() {
        open("x-apple.systempreferences:com.apple.Trackpad-Settings.extension")
    }

    @objc private func openPointerSettings() {
        open("x-apple.systempreferences:com.apple.preference.universalaccess?Mouse")
    }

    @objc private func openGitHub() {
        open("https://github.com/usmannoor90/mac-windows-trackpad")
    }

    @objc private func quit() { NSApp.terminate(nil) }

    private func open(_ url: String) {
        if let u = URL(string: url) { NSWorkspace.shared.open(u) }
    }

    private func alert(_ message: String, _ detail: String) {
        let a = NSAlert()
        a.messageText = message
        a.informativeText = detail
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }
}
