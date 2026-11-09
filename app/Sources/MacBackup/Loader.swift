import Foundation

/// Reads the engine's files. Everything here is tolerant: missing or broken data gives nil / empty, never a crash.
nonisolated enum Loader {

    struct Snapshot: Sendable {
        var status: Status?
        var diskMounted: Bool
        var diskFree: Double?
        var engineExists: Bool
        var config: AppConfig
        var index: IndexInfo??      // nil = unchanged since last stamp
        var indexStamp: String
        var runs: RunsData?         // nil = unchanged
        var runsStamp: String
    }

    @concurrent
    static func snapshot(_ paths: AppPaths, indexStamp: String?, runsStamp: String?) async -> Snapshot {
        let fm = FileManager.default
        let config = AppConfig.load(paths.config)
        let disk = paths.disk(config)
        let mounted = isDiskMounted(disk, test: paths.isTest)
        var free: Double?
        if mounted, let v = try? disk.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let cap = v.volumeAvailableCapacityForImportantUsage {
            free = Double(cap)
        }

        let diskIndex = paths.diskIndex(config)
        let indexURL = (mounted && fm.fileExists(atPath: diskIndex.path)) ? diskIndex : paths.indexMirror
        let iStamp = stamp(indexURL)
        let index: IndexInfo?? = (iStamp == indexStamp) ? nil : .some(loadIndex(indexURL, fromDisk: indexURL == diskIndex))

        let rStamp = stamp(paths.runsLog)
        let runs: RunsData? = (rStamp == runsStamp) ? nil : loadRuns(paths.runsLog)

        return Snapshot(status: loadStatus(paths.statusFile), diskMounted: mounted, diskFree: free,
                        engineExists: fm.fileExists(atPath: paths.engine.path), config: config,
                        index: index, indexStamp: iStamp, runs: runs, runsStamp: rStamp)
    }

    static func isDiskMounted(_ disk: URL, test: Bool) -> Bool {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: disk.path, isDirectory: &isDir), isDir.boolValue else { return false }
        if test { return true }
        let v = try? disk.resourceValues(forKeys: [.isVolumeKey])
        return v?.isVolume == true
    }

    static func stamp(_ url: URL) -> String {
        guard let a = try? FileManager.default.attributesOfItem(atPath: url.path) else { return "\(url.path)|none" }
        let m = (a[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
        let s = (a[.size] as? NSNumber)?.int64Value ?? 0
        return "\(url.path)|\(m)|\(s)"
    }

    // MARK: status.json

    static func loadStatus(_ url: URL) -> Status? {
        guard let d = try? Data(contentsOf: url), let j = J(data: d) else { return nil }
        var s = Status()
        s.state = j.str("state") ?? "idle"
        s.runID = j.str("run_id")
        s.mode = j.str("mode")
        s.startedAt = j.date("started_at")
        s.updatedAt = j.date("updated_at")
        s.finishedAt = j.date("finished_at")
        s.pendingSince = j.date("pending_since")
        s.diskMounted = j.bool("disk_mounted")
        s.diskFreeBytes = j.num("disk_free_bytes")
        s.phase = j.str("phase")
        s.currentUnit = j.str("current_unit")
        s.unitsTotal = j.int("units_total") ?? 0
        s.unitsDone = j.int("units_done") ?? 0
        s.unitsSkipped = j.int("units_skipped_unchanged") ?? 0
        s.unitsFailed = j.int("units_failed") ?? 0
        s.bytesTotal = j.num("bytes_total") ?? 0
        s.bytesDone = j.num("bytes_done") ?? 0
        s.speedBps = j.num("speed_bps")
        s.etaSeconds = j.num("eta_seconds")
        s.lastSuccessAt = j.date("last_success_at")
        s.message = j.str("message")
        s.pid = j.int("pid").map { Int32(clamping: $0) }
        return s
    }

    // MARK: index.json

    static func loadIndex(_ url: URL, fromDisk: Bool) -> IndexInfo? {
        guard let d = try? Data(contentsOf: url), let j = J(data: d) else { return nil }
        var units: [UnitInfo] = []
        if let dict = j.raw["units"] as? [String: Any] {
            for (key, value) in dict {
                guard let u = value as? [String: Any] else { continue }
                let e = J(u)
                let title = e.str("title") ?? key.split(separator: "/").last.map(String.init) ?? key
                units.append(UnitInfo(
                    id: key, layer: Layer(raw: e.str("layer") ?? key.split(separator: "/").first.map(String.init)),
                    title: title, source: e.str("source") ?? "", files: e.int("files") ?? 0,
                    bytesRaw: e.num("bytes_raw") ?? 0, bytesArchive: e.num("bytes_archive") ?? 0,
                    createdAt: e.date("created_at"), verifiedAt: e.date("verified_at"),
                    verifyOK: e.bool("verify_ok"), skippedLarge: e.int("skipped_large_files") ?? 0,
                    archive: e.str("archive") ?? ""))
            }
        }
        units.sort { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        return IndexInfo(updatedAt: j.date("updated_at"), units: units, fromDisk: fromDisk)
    }

    // MARK: runs.jsonl

    static func loadRuns(_ url: URL) -> RunsData {
        guard let data = try? Data(contentsOf: url), let text = String(data: data, encoding: .utf8) else { return RunsData() }
        var lastRunID: String?
        var accessWarnRuns: Set<String> = []
        var starts: [String: (Date?, String?)] = [:]
        var failures: [String: [UnitFailure]] = [:]
        var ends: [String: RunSummary] = [:]
        for line in text.split(whereSeparator: \.isNewline) {
            guard let lineData = line.data(using: .utf8), let j = J(data: lineData),
                  let event = j.str("event"), let runID = j.str("run_id") else { continue }
            lastRunID = runID
            switch event {
            case "run_start":
                starts[runID] = (j.date("ts"), j.str("mode"))
            case "unit_fail", "verify_fail":
                failures[runID, default: []].append(UnitFailure(
                    unit: j.str("unit") ?? "—", error: j.str("error") ?? j.str("message"), isVerify: event == "verify_fail"))
            case "run_done", "run_fail", "run_cancel":
                let outcome: RunOutcome = event == "run_done" ? .done : (event == "run_fail" ? .failed : .cancelled)
                ends[runID] = RunSummary(
                    id: runID, outcome: outcome, startedAt: nil, endedAt: j.date("ts"), mode: j.str("mode"),
                    seconds: j.num("seconds"), unitsDone: j.int("units_done"),
                    unitsSkipped: j.int("units_skipped_unchanged"), unitsFailed: j.int("units_failed"),
                    bytesRaw: j.num("bytes_raw"), bytesArchive: j.num("bytes_archive"),
                    error: j.str("error") ?? j.str("message"), failures: [])
            case "warn":
                let m = j.str("message") ?? j.str("error") ?? ""
                if ["Нет доступа", "No access", "Full Disk Access"].contains(where: { m.contains($0) }) { accessWarnRuns.insert(runID) }
            default: break
            }
        }
        var result: [RunSummary] = []
        for (runID, var r) in ends {
            if let s = starts[runID] {
                r.startedAt = s.0 ?? DateParse.runID(runID)
                if r.mode == nil { r.mode = s.1 }
            } else {
                r.startedAt = DateParse.runID(runID)
            }
            r.failures = failures[runID] ?? []
            result.append(r)
        }
        result.sort { ($0.endedAt ?? $0.startedAt ?? .distantPast) > ($1.endedAt ?? $1.startedAt ?? .distantPast) }
        return RunsData(runs: result, accessWarning: lastRunID.map { accessWarnRuns.contains($0) } ?? false)
    }

    // MARK: log tail

    @concurrent
    static func logTail(_ url: URL, maxBytes: Int = 200_000) async -> String? {
        guard let h = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? h.close() }
        let size = (try? h.seekToEnd()) ?? 0
        let start = size > UInt64(maxBytes) ? size - UInt64(maxBytes) : 0
        try? h.seek(toOffset: start)
        guard let d = try? h.readToEnd() else { return "" }
        var s = String(decoding: d, as: UTF8.self)
        if start > 0, let nl = s.firstIndex(of: "\n") {
            s = "…\n" + s[s.index(after: nl)...]
        }
        return s
    }
}

/// Tiny tolerant accessor over a JSON object.
nonisolated struct J {
    let raw: [String: Any]
    init(_ raw: [String: Any]) { self.raw = raw }
    init?(data: Data) {
        guard let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        raw = o
    }
    func str(_ k: String) -> String? {
        if let s = raw[k] as? String { return s.isEmpty ? nil : s }
        return nil
    }
    func num(_ k: String) -> Double? {
        if let n = raw[k] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() { return n.doubleValue.isFinite ? n.doubleValue : nil }
        if let s = raw[k] as? String { return Double(s) }
        return nil
    }
    func int(_ k: String) -> Int? { num(k).map { Int(clamping: Int64($0.rounded())) } }
    func bool(_ k: String) -> Bool? {
        if let b = raw[k] as? Bool { return b }
        if let n = raw[k] as? NSNumber { return n.intValue != 0 }
        return nil
    }
    func date(_ k: String) -> Date? { str(k).flatMap(DateParse.iso) }
}

nonisolated enum DateParse {
    private static let withZone = Date.ISO8601FormatStyle()
    private static let noZone = Date.ISO8601FormatStyle(timeZone: .current).year().month().day()
        .dateTimeSeparator(.standard).time(includingFractionalSeconds: false)

    /// Accepts "2026-11-09T19:10:00Z", "+03:00" offsets, fractional seconds of any length, and local times without a zone.
    static func iso(_ s: String) -> Date? {
        var t = s.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: " ", with: "T")
        if let r = t.range(of: #"\.\d+"#, options: .regularExpression) { t.removeSubrange(r) }
        if let d = try? withZone.parse(t) { return d }
        if let d = try? noZone.parse(t) { return d }
        return nil
    }

    /// run_id like "2026-11-09T19-10-00" (local time).
    static func runID(_ s: String) -> Date? {
        let parts = s.split(separator: "T")
        guard parts.count == 2 else { return nil }
        return iso("\(parts[0])T\(parts[1].replacingOccurrences(of: "-", with: ":"))")
    }
}
