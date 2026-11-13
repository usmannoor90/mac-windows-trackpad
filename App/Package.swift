// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "InstantRelease",
    platforms: [.macOS(.v13)],
    targets: [
        // Thin C shim around Apple's private MultitouchSupport framework,
        // loaded with dlopen so nothing private is linked at build time.
        .target(name: "CMultitouch", path: "Sources/CMultitouch"),
        // The gesture state machine. Pure logic, no AppKit, so it can be tested.
        .target(name: "InstantReleaseCore", path: "Sources/InstantReleaseCore"),
        // The menu bar app.
        .executableTarget(
            name: "InstantRelease",
            dependencies: ["InstantReleaseCore", "CMultitouch"],
            path: "Sources/InstantRelease",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("ServiceManagement"),
            ]
        ),
        // Gesture tests as a plain executable (XCTest needs full Xcode).
        .executableTarget(
            name: "GestureTests",
            dependencies: ["InstantReleaseCore"],
            path: "Sources/GestureTests"
        ),
    ]
)
