import Foundation

/// Human-readable formatting in the current UI language.
enum Fmt {
    static var lang: Lang { LangState.shared.lang }
    static var locale: Locale { lang.locale }

    static func bytes(_ value: Double?) -> String {
        guard let value, value.isFinite, value >= 0 else { return "—" }
        let units = lang == .ru ? ["Б", "КБ", "МБ", "ГБ", "ТБ"] : ["B", "KB", "MB", "GB", "TB"]
        var v = value
        var i = 0
        while v >= 1000, i < units.count - 1 { v /= 1000; i += 1 }
        let digits = (i == 0 || v >= 100) ? 0 : 1
        return v.formatted(.number.precision(.fractionLength(0...digits)).locale(locale)) + " " + units[i]
    }

    static func speed(_ bps: Double?) -> String {
        guard let bps, bps > 0, bps.isFinite else { return "—" }
        let mb = bps / 1_000_000
        return mb.formatted(.number.precision(.fractionLength(0...(mb < 10 ? 1 : 0))).locale(locale)) + tr(" MB/s", " МБ/с")
    }

    static func ratio(_ r: Double) -> String {
        guard r > 0, r.isFinite else { return "—" }
        return "×" + r.formatted(.number.precision(.fractionLength(1)).locale(locale))
    }

    static func percent(_ p: Double) -> String {
        "\(Int((min(max(p, 0), 1) * 100).rounded(.down)))%"
    }

    private static func format(_ d: Date, _ template: String) -> String {
        let f = DateFormatter()
        f.locale = locale
        f.setLocalizedDateFormatFromTemplate(template)
        return f.string(from: d)
    }

    /// "November 9, 6:40 PM" / "9 ноября, 18:40" (year added if not this year).
    static func dateLong(_ d: Date?) -> String {
        guard let d else { return "—" }
        let sameYear = Calendar.current.isDate(d, equalTo: Date(), toGranularity: .year)
        return format(d, sameYear ? "MMMMdjmm" : "yMMMMdjmm")
    }

    /// "Nov 9, 6:40 PM" / "9 нояб., 18:40"
    static func dateShort(_ d: Date?) -> String {
        guard let d else { return "—" }
        let sameYear = Calendar.current.isDate(d, equalTo: Date(), toGranularity: .year)
        return sameYear ? format(d, "MMMdjmm") : format(d, "yMMMd")
    }

    /// "2 days ago", "today", "yesterday".
    static func relative(_ d: Date, now: Date = Date()) -> String {
        let cal = Calendar.current
        let secs = now.timeIntervalSince(d)
        if secs < 60 { return tr("just now", "только что") }
        if secs < 3600 * 6 {
            let f = RelativeDateTimeFormatter()
            f.locale = locale
            f.unitsStyle = .full
            return f.localizedString(for: d, relativeTo: now)
        }
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: d), to: cal.startOfDay(for: now)).day ?? 0
        switch days {
        case ..<1: return tr("today", "сегодня")
        case 1: return tr("yesterday", "вчера")
        default: return tr("\(days) days ago", "\(days) \(plural(days, "день", "дня", "дней")) назад")
        }
    }

    static func days(_ n: Int) -> String {
        tr(n == 1 ? "1 day" : "\(n) days", "\(n) \(plural(n, "день", "дня", "дней"))")
    }

    static func plural(_ n: Int, _ one: String, _ few: String, _ many: String) -> String {
        let n10 = n % 10, n100 = n % 100
        if n10 == 1 && n100 != 11 { return one }
        if (2...4).contains(n10) && !(12...14).contains(n100) { return few }
        return many
    }

    /// "12 min", "1 h 05 min", "45 s"
    static func duration(_ s: Double?) -> String {
        guard let s, s.isFinite, s >= 0 else { return "—" }
        let total = Int(s.rounded())
        if total < 60 { return tr("\(total) s", "\(total) с") }
        let h = total / 3600, m = (total % 3600) / 60
        if h == 0 { return tr("\(m) min", "\(m) мин") }
        return tr("\(h) h \(String(format: "%02d", m)) min", "\(h) ч \(String(format: "%02d", m)) мин")
    }

    static func eta(_ s: Double?) -> String? {
        guard let s, s.isFinite, s >= 0 else { return nil }
        if s < 60 { return tr("less than a minute left", "осталось меньше минуты") }
        let m = Int((s / 60).rounded(.up))
        if m < 90 { return tr("about \(m) min left", "осталось ~\(m) мин") }
        let hm = String(format: "%02d", m % 60)
        return tr("about \(m / 60) h \(hm) min left", "осталось ~\(m / 60) ч \(hm) мин")
    }

    static func phase(_ p: String?) -> String? {
        switch p {
        case "scan": tr("Looking for changes", "Смотрю, что изменилось")
        case "archive": tr("Compressing and packing", "Сжимаю и упаковываю")
        case "verify": tr("Verifying archives", "Проверяю архивы")
        case "copy": tr("Copying to the disk", "Переношу на диск")
        case "finish": tr("Finishing", "Заканчиваю")
        default: nil
        }
    }

    static func mode(_ m: String?) -> String {
        switch m {
        case "auto": tr("automatic", "авто")
        case "manual": tr("manual", "вручную")
        default: "—"
        }
    }
}
