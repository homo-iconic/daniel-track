import AppKit
import Foundation

final class AlertApp: NSObject, NSApplicationDelegate {
    let titleText: String
    let messageText: String
    var panel: NSPanel!
    var sound: NSSound?
    var timer: Timer?
    let pidURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("track/motion_alert.pid")

    init(title: String, message: String) {
        self.titleText = title
        self.messageText = message
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? "\(ProcessInfo.processInfo.processIdentifier)".write(to: pidURL, atomically: true, encoding: .utf8)

        let width: CGFloat = 360
        let height: CGFloat = 148
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "Daddy Tracker"
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = NSColor(calibratedWhite: 0.11, alpha: 0.96)

        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: f.maxX - width - 18, y: f.maxY - height - 12))
        }

        let root = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        let title = NSTextField(labelWithString: titleText)
        title.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        title.textColor = .white
        title.frame = NSRect(x: 16, y: height - 42, width: width - 32, height: 22)

        let body = NSTextField(wrappingLabelWithString: messageText)
        body.font = NSFont.systemFont(ofSize: 12)
        body.textColor = NSColor(calibratedWhite: 0.72, alpha: 1)
        body.frame = NSRect(x: 16, y: 52, width: width - 32, height: 52)

        let button = NSButton(title: "Dismiss", target: self, action: #selector(dismiss))
        button.bezelStyle = .rounded
        button.keyEquivalent = "\r"
        button.frame = NSRect(x: width - 108, y: 14, width: 90, height: 28)

        root.addSubview(title)
        root.addSubview(body)
        root.addSubview(button)
        panel.contentView = root
        panel.delegate = self

        startChime()
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func startChime() {
        sound = NSSound(named: NSSound.Name("Submarine"))
        sound?.volume = 1.0
        sound?.play()
        timer = Timer.scheduledTimer(withTimeInterval: 2.4, repeats: true) { [weak self] _ in
            self?.sound?.stop()
            self?.sound = NSSound(named: NSSound.Name("Submarine"))
            self?.sound?.play()
        }
    }

    @objc func dismiss() {
        timer?.invalidate()
        sound?.stop()
        try? FileManager.default.removeItem(at: pidURL)
        panel.close()
        NSApp.terminate(nil)
    }
}

extension AlertApp: NSWindowDelegate {
    func windowWillClose(_ notification: Notification) {
        timer?.invalidate()
        sound?.stop()
        try? FileManager.default.removeItem(at: pidURL)
        NSApp.terminate(nil)
    }
}

let title = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "Motion detected"
let message = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "Daniel moved"
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AlertApp(title: title, message: message)
app.delegate = delegate
app.run()
