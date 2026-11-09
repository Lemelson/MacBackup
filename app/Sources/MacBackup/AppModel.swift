import AppKit
import Foundation
import Observation
import UserNotifications

enum Freshness { case ok, due, overdue, never }

enum LiveState: Equatable {
    case idle
    case running        // backup in progress
    case verifying      // archive check in progress
    case stalled        // status says running but the engine is gone / silent
    case interrupted    // disk was disconnected mid-run; engine resumes on next mount
}

struct AlertInfo: Identifiable {
    let id = UUID()
    let title: String
    let text: String
}

@Observable
final class AppModel {
    let paths: AppPaths
    private(set) var config = AppConfig()
    private(set) var status: Status?
    private(set) var index: IndexInfo?
    private(set) var runs: [RunSummary] = []
    private(set) var accessWarning = false
    private(set) var diskMounted = false
    private(set) var diskFree: Double?
    private(set) var engineExists = true
    private(set) var loadedOnce = false
    private(set) var now = Date()
    var alert: AlertInfo?
    /// Set right after we launch the engine, until status.json reflects it.
    private(set) var launchingSince: Date?

    @ObservationIgnored private var indexStamp: String?
    @ObservationIgnored private var runsStamp: String?
    @ObservationIgnored private var processes: [Process] = []
    @ObservationIgnored private var loop: Task<Void, Never>?

    init(paths: AppPaths) {
        self.paths = paths
        config = AppConfig.load(paths.config)
        LangState.shared.lang = config.language
        loop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func refresh() async {
        let snap = await Loader.snapshot(paths, indexStamp: indexStamp, runsStamp: runsStamp)
        if config != snap.config {
            let diskChanged = config.diskVolume != snap.config.diskVolume || config.destFolder != snap.config.destFolder
            config = snap.config
            if LangState.shared.lang != config.language { LangState.shared.lang = config.language }
            if diskChanged { indexStamp = nil }
        }
        if status != snap.status { status = snap.status }
        if diskMounted != snap.diskMounted { diskMounted = snap.diskMounted }
        if let a = diskFree, let b = snap.diskFree, abs(a - b) < 50_000_000 {} else if diskFree != snap.diskFree { diskFree = snap.diskFree }
        if engineExists != snap.engineExists { engineExists = snap.engineExists }
        if let idx = snap.index { index = idx; indexStamp = snap.indexStamp }
        if let r = snap.runs {
            runs = r.runs
            accessWarning = r.accessWarning
            runsStamp = snap.runsStamp
        }
        if let since = launchingSince, liveState != .idle || Date().timeIntervalSince(since) > 15 { launchingSince = nil }
        let t = Date()
        if t.timeIntervalSince(now) > 20 || !loadedOnce { now = t }
        loadedOnce = true
    }

    // MARK: settings

    var diskName: String { paths.disk(config).lastPathComponent }

    func setLanguage(_ lang: Lang) {
        LangState.shared.lang = lang
        config.language = lang
        save(["language": lang.rawValue])
    }

    func setDiskVolume(_ path: String) {
        config.diskVolume = path
        indexStamp = nil
        save(["disk_volume": path])
        Task { await refresh() }
    }

    func setDueDays(_ n: Int) { config.dueDays = n; save(["due_days": n]) }
    func setGraceDays(_ n: Int) { config.graceDays = n; save(["reminder_grace_days": n]) }

    private func save(_ changes: [String: Any]) {
        do { try AppConfig.update(paths.config, changes) } catch {
            alert = AlertInfo(title: tr("Couldn't save settings", "Не получилось сохранить настройки"),
                              text: "\(paths.config.path)\n\(error.localizedDescription)")
        }
    }

    static func requestNotificationPermission() {
        guard Bundle.main.bundleIdentifier != nil else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    // MARK: derived state

    var liveState: LiveState {
        guard let s = status else { return .idle }
        if s.state == "interrupted" { return .interrupted }
        guard s.isBusyState else { return .idle }
        if let u = s.updatedAt, Date().timeIntervalSince(u) > 120 { return .stalled }
        if let pid = s.pid, pid > 0, kill(pid, 0) != 0, errno == ESRCH { return .stalled }
        return s.state == "verifying" ? .verifying : .running
    }

    var isActive: Bool { liveState == .running || liveState == .verifying }

    var lastSuccess: Date? {
        let fromRuns = runs.first(where: { $0.outcome == .done })?.endedAt
        switch (status?.lastSuccessAt, fromRuns) {
        case let (a?, b?): return max(a, b)
        case let (a?, nil): return a
        case let (nil, b?): return b
        default: return nil
        }
    }

    /// Green within due_days, amber up to twice that, red after.
    var freshness: Freshness {
        guard let d = lastSuccess else { return .never }
        let days = now.timeIntervalSince(d) / 86_400
        let due = Double(max(config.dueDays, 1))
        if days <= due { return .ok }
        if days <= due * 2 { return .due }
        return .overdue
    }

    var progress: Double {
        guard let s = status, s.bytesTotal > 0 else { return 0 }
        return min(max(s.bytesDone / s.bytesTotal, 0), 1)
    }

    /// Last finished attempt, if it was not a success (to warn about it).
    var lastProblem: RunSummary? {
        guard let r = runs.first, r.outcome != .done else { return nil }
        return r
    }

    /// Why "Back Up Now" is unavailable (nil = available).
    var startBlockReason: String? {
        if !engineExists { return tr("The backup engine is missing: \(paths.engine.path)", "Не найдена программа копирования: \(paths.engine.path)") }
        if launchingSince != nil { return tr("Starting…", "Запускаю…") }
        switch liveState {
        case .running: return tr("A backup is already running.", "Копия уже делается.")
        case .verifying: return tr("Archives are being verified right now.", "Сейчас идёт проверка архивов.")
        default: break
        }
        if !diskMounted { return tr("Connect the backup disk “\(diskName)” to make a backup.", "Подключите диск «\(diskName)», чтобы сделать копию.") }
        return nil
    }

    // MARK: actions

    func startBackup() { launch(["run", "--mode", "manual"], what: tr("the backup", "копию")) }
    func startVerify() { launch(["verify", "--all"], what: tr("the check", "проверку")) }
    func cancel() { launch(["cancel"], what: tr("stopping", "остановку"), trackLaunch: false) }

    func openBackupFolder() {
        let dir = paths.backupDir(config)
        if FileManager.default.fileExists(atPath: dir.path) {
            NSWorkspace.shared.open(dir)
        } else {
            alert = AlertInfo(title: tr("Folder not found", "Папка не найдена"),
                              text: diskMounted
                                ? tr("There is no \(config.destFolder) folder on “\(diskName)” yet. It appears after the first backup.",
                                     "На диске «\(diskName)» ещё нет папки \(config.destFolder). Она появится после первой копии.")
                                : tr("The backup disk “\(diskName)” is not connected.", "Диск «\(diskName)» не подключён."))
        }
    }

    func openReadme() {
        let url = paths.readme(config)
        if FileManager.default.fileExists(atPath: url.path) {
            NSWorkspace.shared.open(url)
        } else {
            alert = AlertInfo(title: tr("README not found", "Инструкция не найдена"),
                              text: diskMounted
                                ? tr("README.md appears on the disk after the first backup.", "Файл README.md появится на диске после первой копии.")
                                : tr("The README is on the backup disk “\(diskName)”, which is not connected.",
                                     "Инструкция лежит на диске «\(diskName)», а он не подключён."))
        }
    }

    func openLog(_ runID: String) {
        let url = paths.runLog(runID)
        if FileManager.default.fileExists(atPath: url.path) { NSWorkspace.shared.open(url) }
    }

    nonisolated static func childEnvironment(_ paths: AppPaths) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["MACBACKUP_DATA"] = paths.data.path
        env["MACBACKUP_APP"] = Bundle.main.executablePath   // engine calls `<app> --notify` for notifications
        if let d = paths.diskOverride { env["MACBACKUP_DISK"] = d }
        return env
    }

    /// An engine started by this app is still running (it runs as our child, so it shares our Full Disk Access).
    var hasRunningChild: Bool { processes.contains { $0.isRunning && $0.arguments?.contains("cancel") == false } }

    /// Used when quitting during a run: ask the engine to stop and wait for our children to exit.
    func stopAndWait(timeout: TimeInterval) {
        let c = Process()
        c.executableURL = URL(filePath: paths.python)
        c.arguments = [paths.engine.path, "cancel"]
        c.environment = Self.childEnvironment(paths)
        c.standardOutput = FileHandle.nullDevice
        c.standardError = FileHandle.nullDevice
        try? c.run()
        c.waitUntilExit()
        let deadline = Date().addingTimeInterval(timeout)
        while hasRunningChild && Date() < deadline { Thread.sleep(forTimeInterval: 0.2) }
        for p in processes where p.isRunning { p.terminate() }
    }

    private func launch(_ args: [String], what: String, trackLaunch: Bool = true) {
        guard FileManager.default.fileExists(atPath: paths.engine.path) else {
            alert = AlertInfo(title: tr("Couldn't start \(what)", "Не получилось запустить \(what)"),
                              text: tr("The backup engine is missing:", "Не найдена программа копирования:") + "\n\(paths.engine.path)")
            return
        }
        let fm = FileManager.default
        try? fm.createDirectory(at: paths.logsDir, withIntermediateDirectories: true)
        if !fm.fileExists(atPath: paths.launchLog.path) { fm.createFile(atPath: paths.launchLog.path, contents: nil) }

        let p = Process()
        p.executableURL = URL(filePath: paths.python)
        p.arguments = [paths.engine.path] + args
        p.currentDirectoryURL = paths.engine.deletingLastPathComponent()
        p.environment = Self.childEnvironment(paths)
        p.standardInput = FileHandle.nullDevice

        var logStart: UInt64 = 0
        if let h = try? FileHandle(forWritingTo: paths.launchLog) {
            logStart = (try? h.seekToEnd()) ?? 0
            let header = "\n=== \(Date().formatted(.iso8601)) MacBackup.app: \(args.joined(separator: " ")) ===\n"
            h.write(Data(header.utf8))
            logStart += UInt64(header.utf8.count)
            p.standardOutput = h
            p.standardError = h
        }

        let started = Date()
        let launchLog = paths.launchLog
        let offset = logStart
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor in
                self?.finished(proc, code: code, started: started, what: what, log: launchLog, logStart: offset)
            }
        }
        do {
            try p.run()
            processes.append(p)
            if trackLaunch { launchingSince = Date() }
        } catch {
            alert = AlertInfo(title: tr("Couldn't start \(what)", "Не получилось запустить \(what)"),
                              text: tr("Python doesn't start:", "Не запускается Python:") + " \(paths.python)\n\(error.localizedDescription)")
        }
    }

    private func finished(_ p: Process, code: Int32, started: Date, what: String, log: URL, logStart: UInt64) {
        processes.removeAll { $0 === p }
        launchingSince = nil
        Task { await refresh() }
        guard code != 0 else { return }
        let output = Self.readTail(log, from: logStart)
        if code == 3 {
            alert = AlertInfo(title: tr("A backup is already running", "Копия уже идёт"),
                              text: tr("The backup engine is already working, no need to start it twice. Wait for it to finish.",
                                       "Программа копирования уже работает, второй раз её запускать не нужно. Дождитесь конца.") + "\n\n\(output)")
        } else if Date().timeIntervalSince(started) < 120 {
            alert = AlertInfo(title: tr("Couldn't start \(what)", "Не получилось запустить \(what)"),
                              text: tr("The backup engine stopped right away (code \(code)).",
                                       "Программа копирования сразу остановилась (код \(code)).") + "\n\n\(output)")
        }
    }

    private static func readTail(_ url: URL, from offset: UInt64) -> String {
        guard let h = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? h.close() }
        try? h.seek(toOffset: offset)
        let s = String(decoding: (try? h.readToEnd()) ?? Data(), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return String(s.suffix(600))
    }
}
