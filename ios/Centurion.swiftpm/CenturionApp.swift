import SwiftUI
import WebKit

// Centurion for iOS: a thin native shell around the web app on GitHub Pages.
// Everything the user sees is the web app; this file only supplies what a bare
// WKWebView lacks — alert()/confirm() dialogs, external links, pull-to-refresh,
// crash/offline recovery, and the centurion:// setup link.
//
// Apple Watch data does NOT come through here: an App Playground cannot carry the
// HealthKit entitlement. The phone pushes Health data to the home sync server
// instead (see the repo README), and the web app receives it through sync.

enum Version {
    static let current = "1.0.0"   // keep in step with displayVersion in Package.swift
}

@main
@MainActor
struct CenturionApp: App {
    @StateObject private var web = WebModel()

    var body: some Scene {
        WindowGroup {
            ContentView(web: web)
                .preferredColorScheme(.dark)
                .onOpenURL { url in web.open(setupLink: url) }
        }
    }
}

struct ContentView: View {
    @ObservedObject var web: WebModel
    private let background = Color(red: 10 / 255, green: 10 / 255, blue: 12 / 255)

    var body: some View {
        ZStack {
            background.ignoresSafeArea()
            WebContainer(webView: web.webView)
            if let message = web.failure {
                VStack(spacing: 14) {
                    Text("Can't load Centurion")
                        .font(.headline)
                        .foregroundColor(.white)
                    Text(message)
                        .font(.footnote)
                        .foregroundColor(.gray)
                        .multilineTextAlignment(.center)
                    Button("Try again") { web.loadHome() }
                        .buttonStyle(.borderedProminent)
                }
                .padding(28)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(background)
            }
        }
    }
}

struct WebContainer: UIViewRepresentable {
    let webView: WKWebView

    func makeUIView(context: Context) -> WKWebView {
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
    }
}

@MainActor
final class WebModel: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate {
    static let home = URL(string: "https://jclarke916.github.io/Health-App1/")!

    @Published var failure: String? = nil
    let webView: WKWebView

    override init() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore.default()   // persistent: localStorage survives relaunch
        config.allowsInlineMediaPlayback = true
        // Puts "Centurion" in the User-Agent. The web app keys off it to hide its
        // "add to home screen" offer: we are already the installed app.
        config.applicationNameForUserAgent = "Centurion/" + Version.current
        let view = WKWebView(frame: .zero, configuration: config)
        view.isOpaque = false
        view.backgroundColor = UIColor(red: 10 / 255, green: 10 / 255, blue: 12 / 255, alpha: 1)
        view.scrollView.backgroundColor = view.backgroundColor
        view.allowsBackForwardNavigationGestures = false
        view.allowsLinkPreview = false
        self.webView = view
        super.init()

        view.navigationDelegate = self
        view.uiDelegate = self
        let refresh = UIRefreshControl()
        refresh.tintColor = UIColor.lightGray
        refresh.addTarget(self, action: #selector(pulledToRefresh(_:)), for: .valueChanged)
        view.scrollView.refreshControl = refresh
        loadHome()
    }

    func loadHome() {
        failure = nil
        // Always ask GitHub whether the page changed: the web app updates by a push, and a
        // cached copy hid a fix from an iPad for a day. Unchanged pages still come from cache.
        webView.load(URLRequest(url: WebModel.home, cachePolicy: .reloadRevalidatingCacheData))
    }

    @objc private func pulledToRefresh(_ sender: UIRefreshControl) {
        failure = nil
        webView.reloadFromOrigin()
        sender.endRefreshing()
    }

    /// centurion://setup#<base64 JSON> -> the web app's own #setup= handler (which asks before accepting).
    /// A changed query string forces a real page load; a changed fragment alone would not.
    func open(setupLink url: URL) {
        guard url.scheme == "centurion", let fragment = url.fragment, !fragment.isEmpty else { return }
        let stamp = Int(Date().timeIntervalSince1970)
        let text = WebModel.home.absoluteString + "?setup=\(stamp)#setup=" + fragment
        if let target = URL(string: text) {
            failure = nil
            webView.load(URLRequest(url: target))
        }
    }

    // MARK: navigation

    // The async forms of these delegate methods are used on purpose: the completion-handler
    // forms changed their closure attributes between SDKs (@MainActor was added), and a
    // signature that "nearly matches" is silently never called.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
        let url = navigationAction.request.url
        let isTap = navigationAction.navigationType == .linkActivated
        let staysHere = url?.host == WebModel.home.host
        if isTap, !staysHere, let outside = url, outside.scheme == "https" || outside.scheme == "http" {
            _ = await UIApplication.shared.open(outside)
            return .cancel
        }
        return .allow
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        failure = nil
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        report(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        report(error)
    }

    private func report(_ error: Error) {
        let code = (error as NSError).code
        if code == NSURLErrorCancelled { return }   // a newer load replaced this one
        failure = error.localizedDescription
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()   // iOS killed the page for memory; localStorage is intact
    }

    // MARK: window.open / alert / confirm — a bare WKWebView silently drops all three

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            UIApplication.shared.open(url)
        }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo) async {
        guard let presenter = topController() else { return }
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in done.resume() })
            presenter.present(alert, animated: true)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo) async -> Bool {
        guard let presenter = topController() else { return false }
        return await withCheckedContinuation { (done: CheckedContinuation<Bool, Never>) in
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in done.resume(returning: false) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in done.resume(returning: true) })
            presenter.present(alert, animated: true)
        }
    }

    private func topController() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let window = scenes.flatMap { $0.windows }.first { $0.isKeyWindow }
        var top = window?.rootViewController
        while let next = top?.presentedViewController {
            // Never present on top of a sheet that is going away — the alert would vanish
            // with it and the page would wait forever for an answer.
            if next.isBeingDismissed { break }
            top = next
        }
        return top
    }
}
