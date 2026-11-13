// Draws the app icon (a trackpad with a fingertip) and writes an .icns file.
// Usage: swift make-icon.swift <output.icns>
import AppKit

func draw(size: CGFloat) -> NSImage {
    let img = NSImage(size: NSSize(width: size, height: size))
    img.lockFocus()
    let s = size
    // Background: rounded square with a blue gradient (macOS icon grid inset).
    let inset = s * 0.08
    let bg = NSBezierPath(roundedRect: NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset),
                          xRadius: s * 0.2, yRadius: s * 0.2)
    NSGradient(starting: NSColor(calibratedRed: 0.20, green: 0.55, blue: 1.0, alpha: 1),
               ending: NSColor(calibratedRed: 0.05, green: 0.25, blue: 0.75, alpha: 1))!
        .draw(in: bg, angle: -90)
    // Trackpad: light rounded rectangle.
    let pad = NSBezierPath(roundedRect: NSRect(x: s * 0.22, y: s * 0.26, width: s * 0.56, height: s * 0.42),
                           xRadius: s * 0.06, yRadius: s * 0.06)
    NSColor(calibratedWhite: 0.95, alpha: 0.95).setFill()
    pad.fill()
    // Fingertip: a dot with soft ripple rings (the "instant tap").
    let c = NSPoint(x: s * 0.5, y: s * 0.47)
    for (i, alpha) in [0.18, 0.32].enumerated() {
        let r = s * (0.13 + CGFloat(i) * 0.06)
        NSColor(calibratedRed: 0.1, green: 0.4, blue: 0.95, alpha: alpha).setFill()
        NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)).fill()
    }
    let r = s * 0.075
    NSColor(calibratedRed: 0.05, green: 0.3, blue: 0.85, alpha: 1).setFill()
    NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)).fill()
    img.unlockFocus()
    return img
}

func png(_ image: NSImage, pixels: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: pixels, height: pixels)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let out = CommandLine.arguments[1]
let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("AppIcon.iconset")
try? FileManager.default.removeItem(at: tmp)
try! FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let px = base * scale
        let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
        try! png(draw(size: CGFloat(px)), pixels: px).write(to: tmp.appendingPathComponent(name))
    }
}
let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
p.arguments = ["-c", "icns", tmp.path, "-o", out]
try! p.run()
p.waitUntilExit()
exit(p.terminationStatus)
