import Foundation

/// Settings shared with the engine: <data>/config.json.
/// The app only touches the keys it knows; every other key is preserved on save.
nonisolated struct AppConfig: Sendable, Equatable {
    var language: Lang = .en
    var diskVolume = "/Volumes/Backup"
    var destFolder = "MacBackup"
    var dueDays = 7
    var graceDays = 3

    static func load(_ url: URL) -> AppConfig {
        var c = AppConfig()
        guard let d = try? Data(contentsOf: url), let j = J(data: d) else { return c }
        if let l = j.str("language").flatMap(Lang.init(rawValue:)) { c.language = l }
        if let v = j.str("disk_volume") { c.diskVolume = v }
        if let v = j.str("dest_folder") { c.destFolder = v }
        if let v = j.int("due_days"), v > 0 { c.dueDays = v }
        if let v = j.int("reminder_grace_days"), v >= 0 { c.graceDays = v }
        return c
    }

    /// Read-modify-write: unknown keys written by the engine survive.
    static func update(_ url: URL, _ changes: [String: Any]) throws {
        var dict: [String: Any] = [:]
        if let d = try? Data(contentsOf: url),
           let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { dict = o }
        for (k, v) in changes { dict[k] = v }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: dict, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        try data.write(to: url, options: .atomic)
    }
}

/// Where the app reads data and how it calls the engine.
/// Real mode: ~/Library/Application Support/MacBackup (or MACBACKUP_DATA), engine bundled in the app.
/// Test mode (`--test` or MACBACKUP_TEST=1): <MACBACKUP_TESTDATA or ./testdata>/data + fake_engine.py.
/// Overrides: MACBACKUP_DATA, MACBACKUP_DISK (backup volume), MACBACKUP_ENGINE, MACBACKUP_PYTHON.
nonisolated struct AppPaths: Sendable {
    let data: URL
    let python: String
    let engine: URL
    let isTest: Bool
    let diskOverride: String?

    var config: URL { data.appending(path: "config.json") }
    var statusFile: URL { data.appending(path: "state/status.json") }
    var indexMirror: URL { data.appending(path: "state/index.json") }
    var logsDir: URL { data.appending(path: "logs") }
    var runsLog: URL { logsDir.appending(path: "runs.jsonl") }
    var launchLog: URL { logsDir.appending(path: "app-launch.log") }
    var autoLog: URL { logsDir.appending(path: "auto.log") }
    func runLog(_ runID: String) -> URL { logsDir.appending(path: "run-\(runID).log") }

    func disk(_ c: AppConfig) -> URL { URL(filePath: diskOverride ?? c.diskVolume) }
    func backupDir(_ c: AppConfig) -> URL { disk(c).appending(path: c.destFolder) }
    func diskIndex(_ c: AppConfig) -> URL { backupDir(c).appending(path: "index.json") }
    func readme(_ c: AppConfig) -> URL { backupDir(c).appending(path: "README.md") }

    static func resolve() -> AppPaths {
        let env = ProcessInfo.processInfo.environment
        let fm = FileManager.default
        let isTest = CommandLine.arguments.contains("--test") || env["MACBACKUP_TEST"] == "1"
        func envPath(_ k: String) -> String? { env[k].flatMap { $0.isEmpty ? nil : $0 } }

        let testRoot = URL(filePath: envPath("MACBACKUP_TESTDATA") ?? (fm.currentDirectoryPath + "/testdata"))

        var data = fm.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/MacBackup")
        if isTest { data = testRoot.appending(path: "data") }
        if let v = envPath("MACBACKUP_DATA") { data = URL(filePath: v) }

        var engine = Bundle.main.resourceURL?.appending(path: "engine/macbackup.py")
            ?? URL(filePath: "/nonexistent/macbackup.py")
        if isTest { engine = testRoot.appending(path: "fake_engine.py") }
        if let v = envPath("MACBACKUP_ENGINE") { engine = URL(filePath: v) }

        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        let python = envPath("MACBACKUP_PYTHON")
            ?? candidates.first { fm.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"

        return AppPaths(data: data, python: python, engine: engine, isTest: isTest, diskOverride: envPath("MACBACKUP_DISK"))
    }
}
