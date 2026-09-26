// The parts of secure-fill that need macOS frameworks.
//   sfhelper auth <reason>                             exit 0 approved, 1 denied, 2 unavailable
//   sfhelper paste <bundle-id> <clear-after-s> <0|1>   secret on stdin; exit 0 pasted,
//                                                      3 no Accessibility, 4 app not running, 5 app not frontmost
import AppKit
import Foundation
import LocalAuthentication

func auth(_ reason: String) -> Int32 {
    let ctx = LAContext()
    var err: NSError?
    // deviceOwnerAuthentication: Touch ID, or the login password when biometrics are unavailable.
    guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else { return 2 }
    let done = DispatchSemaphore(value: 0)
    var ok = false
    ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, _ in
        ok = success
        done.signal()
    }
    done.wait()
    return ok ? 0 : 1
}

func key(_ code: CGKeyCode, command: Bool) {
    let src = CGEventSource(stateID: .hidSystemState)
    for down in [true, false] {
        let ev = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: down)!
        if command { ev.flags = .maskCommand }
        ev.post(tap: .cghidEventTap)
    }
}

func paste(bundleID: String, clearAfter: Double, submit: Bool) -> Int32 {
    guard AXIsProcessTrusted() else { return 3 }
    var data = FileHandle.standardInput.readDataToEndOfFile()
    if data.last == 0x0A { data.removeLast() }
    guard let secret = String(data: data, encoding: .utf8),
          let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first
    else { return 4 }

    let pb = NSPasteboard.general
    let previous = pb.string(forType: .string)
    let item = NSPasteboardItem()
    item.setString(secret, forType: .string)
    // nspasteboard.org markers: clipboard managers and history tools skip these items.
    item.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
    item.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.TransientType"))
    pb.clearContents()
    pb.writeObjects([item])
    let mark = pb.changeCount
    defer {
        Thread.sleep(forTimeInterval: clearAfter)
        // Restore only if nobody else wrote to the clipboard meanwhile.
        if pb.changeCount == mark {
            pb.clearContents()
            if let previous { pb.setString(previous, forType: .string) }
        }
    }

    app.activate()
    let deadline = Date().addingTimeInterval(2)
    while NSWorkspace.shared.frontmostApplication?.bundleIdentifier != bundleID {
        if Date() > deadline { return 5 }
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    Thread.sleep(forTimeInterval: 0.2)
    key(9, command: true)  // kVK_ANSI_V
    if submit {
        Thread.sleep(forTimeInterval: 0.2)
        key(36, command: false)  // kVK_Return
    }
    return 0
}

let args = CommandLine.arguments
switch (args.count > 1 ? args[1] : "", args.count) {
case ("auth", 3):
    exit(auth(args[2]))
case ("paste", 5):
    exit(paste(bundleID: args[2], clearAfter: Double(args[3]) ?? 3, submit: args[4] == "1"))
default:
    FileHandle.standardError.write("usage: sfhelper auth <reason> | paste <bundle-id> <clear-after> <0|1>\n".data(using: .utf8)!)
    exit(64)
}
