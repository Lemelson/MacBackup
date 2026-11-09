import Foundation

nonisolated enum Layer: String, CaseIterable, Sendable, Identifiable {
    case keys, ai, projects, system, media, other
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .keys: "key.fill"
        case .ai: "sparkles"
        case .projects: "folder.fill"
        case .system: "gearshape.fill"
        case .media: "photo.fill"
        case .other: "shippingbox.fill"
        }
    }
    var order: Int { Layer.allCases.firstIndex(of: self) ?? 0 }
    init(raw: String?) { self = Layer(rawValue: raw ?? "") ?? .other }
}

extension Layer {
    @MainActor var title: String {
        switch self {
        case .keys: tr("Keys & access", "Ключи и доступы")
        case .ai: tr("Codex & Claude", "Codex и Claude")
        case .projects: tr("Projects", "Проекты")
        case .system: tr("System & settings", "Система и настройки")
        case .media: tr("Media", "Медиа")
        case .other: tr("Other", "Другое")
        }
    }
}

nonisolated struct Status: Sendable, Equatable {
    var state: String = "idle"
    var runID: String?
    var mode: String?
    var startedAt: Date?
    var updatedAt: Date?
    var finishedAt: Date?
    var pendingSince: Date?
    var diskMounted: Bool?
    var diskFreeBytes: Double?
    var phase: String?
    var currentUnit: String?
    var unitsTotal: Int = 0
    var unitsDone: Int = 0
    var unitsSkipped: Int = 0
    var unitsFailed: Int = 0
    var bytesTotal: Double = 0
    var bytesDone: Double = 0
    var speedBps: Double?
    var etaSeconds: Double?
    var lastSuccessAt: Date?
    var message: String?
    var pid: Int32?

    var isBusyState: Bool { state == "running" || state == "verifying" }
}

nonisolated struct UnitInfo: Sendable, Identifiable, Equatable {
    var id: String
    var layer: Layer
    var title: String
    var source: String
    var files: Int
    var bytesRaw: Double
    var bytesArchive: Double
    var createdAt: Date?
    var verifiedAt: Date?
    var verifyOK: Bool?
    var skippedLarge: Int
    var archive: String

    /// Media is copied as a plain folder (path relative to the disk root), not compressed.
    var isPlainFolder: Bool {
        if layer == .media { return true }
        let a = archive.lowercased()
        return !a.isEmpty && !(a.hasSuffix(".zst") || a.hasSuffix(".tar") || a.hasSuffix(".gz") || a.hasSuffix(".zip") || a.hasSuffix(".xz"))
    }

    // Non-optional keys for table sorting.
    var ratio: Double { (bytesArchive > 0 && !isPlainFolder) ? bytesRaw / bytesArchive : 0 }
    var createdSort: Date { createdAt ?? .distantPast }
    var verifySort: Int { verifyOK == nil ? 1 : (verifyOK! ? 2 : 0) }
    var layerSort: Int { layer.order }
}

nonisolated struct IndexInfo: Sendable, Equatable {
    var updatedAt: Date?
    var units: [UnitInfo]
    var fromDisk: Bool
}

nonisolated struct RunsData: Sendable, Equatable {
    var runs: [RunSummary] = []
    /// The newest run logged a "warn" about missing file access (Full Disk Access not granted).
    var accessWarning = false
}

nonisolated struct UnitFailure: Sendable, Equatable, Identifiable {
    var id = UUID()
    var unit: String
    var error: String?
    var isVerify: Bool
}

nonisolated enum RunOutcome: String, Sendable { case done, failed, cancelled }

nonisolated struct RunSummary: Sendable, Equatable, Identifiable {
    var id: String               // run_id
    var outcome: RunOutcome
    var startedAt: Date?
    var endedAt: Date?
    var mode: String?
    var seconds: Double?
    var unitsDone: Int?
    var unitsSkipped: Int?
    var unitsFailed: Int?
    var bytesRaw: Double?
    var bytesArchive: Double?
    var error: String?
    var failures: [UnitFailure]

    var duration: Double? {
        if let seconds { return seconds }
        if let s = startedAt, let e = endedAt { return e.timeIntervalSince(s) }
        return nil
    }
    var averageSpeed: Double? {
        guard let b = bytesRaw, let d = duration, d > 1 else { return nil }
        return b / d
    }
}
