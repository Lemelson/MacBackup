import AppKit
import Foundation
import UserNotifications

/// `MacBackup --auto`: no window, no menu bar item. Runs `macbackup.py auto` as our child process
/// (so macOS privacy checks attribute file access to MacBackup.app), waits, and returns its exit code.
/// Output is appended to <data>/logs/auto.log. Started by launchd hourly and on disk mount.
nonisolated enum AutoRunner {
    static func run(paths: AppPaths) -> Int32 {
        let fm = FileManager.default
        let logURL = paths.autoLog
        try? fm.createDirectory(at: paths.logsDir, withIntermediateDirectories: true)
        if !fm.fileExists(atPath: logURL.path) { fm.createFile(atPath: logURL.path, contents: nil) }
        let log = try? FileHandle(forWritingTo: logURL)
        _ = try? log?.seekToEnd()
        func note(_ s: String) { log?.write(Data("=== \(Date().formatted(.iso8601)) MacBackup.app --auto: \(s)\n".utf8)) }

        guard fm.fileExists(atPath: paths.engine.path) else {
            note("engine not found: \(paths.engine.path)")
            return 127
        }
        let p = Process()
        p.executableURL = URL(filePath: paths.python)
        p.arguments = [paths.engine.path, "auto"]
        p.currentDirectoryURL = paths.engine.deletingLastPathComponent()
        p.environment = AppModel.childEnvironment(paths)
        p.standardInput = FileHandle.nullDevice
        if let log { p.standardOutput = log; p.standardError = log }
        note("start")
        do { try p.run() } catch {
            note("cannot start \(paths.python): \(error.localizedDescription)")
            return 126
        }
        // Forward SIGTERM (launchd stop) to the engine so it can stop gracefully.
        signal(SIGTERM, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
        let pid = p.processIdentifier
        src.setEventHandler { kill(pid, SIGTERM) }
        src.resume()
        p.waitUntilExit()
        let code = p.terminationReason == .exit ? p.terminationStatus : 128 + p.terminationStatus
        note("exit \(code)")
        try? log?.close()
        return code
    }
}

/// `MacBackup --notify "<title>" "<body>"`: posts a notification as MacBackup.app (with its icon) and exits.
nonisolated enum Notifier {
    static func post(title: String, body: String) -> Int32 {
        guard Bundle.main.bundleIdentifier != nil else {
            FileHandle.standardError.write(Data("--notify only works from inside MacBackup.app\n".utf8))
            return 1
        }
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            let center = UNUserNotificationCenter.current()
            var status = await center.notificationSettings().authorizationStatus
            if status == .notDetermined {
                _ = try? await center.requestAuthorization(options: [.alert, .sound])
                status = await center.notificationSettings().authorizationStatus
            }
            if status == .denied {
                FileHandle.standardError.write(Data("notifications are turned off for MacBackup in System Settings\n".utf8))
            }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            do {
                try await center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
            } catch {
                FileHandle.standardError.write(Data("notification failed: \(error.localizedDescription)\n".utf8))
            }
            done.signal()
        }
        _ = done.wait(timeout: .now() + 3)
        Thread.sleep(forTimeInterval: 0.3)
        return 0
    }
}
