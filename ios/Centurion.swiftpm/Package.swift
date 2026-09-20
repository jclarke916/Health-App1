// swift-tools-version: 5.9
// App Playground manifest — open `Centurion.swiftpm` in Swift Playgrounds on iPad (or in Xcode)
// and upload to App Store Connect from the project settings, same route as GCOSScanner.
import PackageDescription
import AppleProductTypes

let package = Package(
    name: "Centurion",
    platforms: [.iOS("16.0")],
    products: [
        .iOSApplication(
            name: "Centurion",
            targets: ["Centurion"],
            // Must match the App Store Connect app record EXACTLY, or the upload is
            // treated as a different app.
            bundleIdentifier: "com.cuttingedge.centurion",
            teamIdentifier: "MW62SS3S6Z",
            // App Store Connect refuses a (version, build) pair it has seen before:
            // bump bundleVersion on EVERY upload, displayVersion when the release changes.
            displayVersion: "1.0.0",
            bundleVersion: "20260919.2",
            // 1024x1024 RGB with NO alpha channel — ASC rejects an icon that has one.
            appIcon: .asset("AppIcon"),
            accentColor: .presetColor(.green),
            supportedDeviceFamilies: [.pad, .phone],
            // All four: ASC rejects iPad builds otherwise ("iPad Multitasking support
            // requires all orientations").
            supportedInterfaceOrientations: [.portrait, .portraitUpsideDown,
                                             .landscapeLeft, .landscapeRight],
            capabilities: [
                .camera(purposeString: "Take a photo of a meal or an InBody / lab report to log it."),
                .photoLibrary(purposeString: "Pick a photo of a meal or an InBody / lab report to log it."),
            ],
            appCategory: .healthcareFitness,
            additionalInfoPlistContentFilePath: "Info.plist"
        )
    ],
    targets: [
        .executableTarget(
            name: "Centurion",
            path: ".",
            exclude: ["Package.swift", "Info.plist"],
            // an asset catalog has to be PROCESSED (compiled into Assets.car); .copy would
            // ship the folder verbatim and the icon would never be found.
            resources: [.process("Assets.xcassets")]
        )
    ]
)
