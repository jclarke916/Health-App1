# Centurion for iOS (TestFlight)

`Centurion.swiftpm` is a Swift Playgrounds app package — the same route GCOSScanner takes to App Store Connect, so **no Mac or Xcode is needed**. It is a native shell around the web app at `https://jclarke916.github.io/Health-App1/`; updating the web app (a push to GitHub) updates what the iOS app shows, with no new TestFlight build.

**Not compiled yet.** There is no Swift toolchain on the Windows box; the files are bracket-checked (`python tools/swift_balance.py ios/Centurion.swiftpm/*.swift`) and modelled on the scanner's working manifest. The first Playgrounds build may surface errors — send them back.

## First upload

1. App Store Connect → Apps → **+ New App**: platform iOS, bundle ID `com.cuttingedge.centurion` (register it under Certificates, IDs & Profiles first if it is not offered), any SKU. The bundle ID must match `Package.swift` exactly.
2. Copy `Centurion.swiftpm.zip` to the iPad (Drive / AirDrop), unzip in Files, open `Centurion.swiftpm` in Swift Playgrounds, run it once.
3. Playgrounds → app settings → **Upload to App Store Connect**. The icon is already in the package (1024×1024, no alpha).
4. In App Store Connect → TestFlight, add yourself and Sophia as internal testers.

Every later upload: bump `bundleVersion` in `Package.swift` (and `displayVersion` + `Version.current` for a new release) — App Store Connect refuses a pair it has seen.

## What the shell adds over Safari

`alert()` / `confirm()` dialogs (a bare WKWebView drops them, and the app uses `confirm` for the setup link and backup import), external links open in Safari, pull-to-refresh, an offline/retry screen, reload after iOS kills the page, camera + photo permissions for meal and scan photos, and `centurion://setup#<base64>` which hands the sync + AI server addresses to the web app (it asks before accepting).

Its storage is separate from Safari's. Turn on Home Sync in the Safari copy first; the app then pulls everything down on its first sync.

Not supported in the shell: **Export backup** (a blob download). Use Safari for that, or rely on Home Sync.

## Apple Watch / Apple Health

An App Playground cannot carry the HealthKit entitlement, so this app cannot read Health. Watch data is pushed to the home sync server instead (`POST /apple?user=jermaine|sophia`, see the top-level README) by an iOS Shortcuts automation or the Health Auto Export app, and both phones receive it through sync. Reading HealthKit natively would need a real Xcode project built on a Mac (or a cloud Mac CI service).
