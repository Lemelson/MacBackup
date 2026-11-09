import AppKit
import SwiftUI

enum MainTab: String, CaseIterable, Identifiable {
    case contents, history
    var id: String { rawValue }
    var title: String { self == .contents ? tr("What's in the backup", "Что в копии") : tr("History", "История") }
}

struct ContentView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openSettings) private var openSettings
    // `--tab=history` opens straight on History (handy for screenshots).
    @State private var tab: MainTab = CommandLine.arguments.contains("--tab=history") ? .history : .contents

    var body: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 14) {
            HeaderView()
            ActionsView()
            if model.accessWarning { AccessBanner() }
            switch model.liveState {
            case .running, .verifying: ProgressCard()
            case .stalled: StalledCard()
            case .interrupted: InterruptedCard()
            case .idle: if let p = model.lastProblem { ProblemCard(run: p) }
            }
            HStack {
                Picker("", selection: $tab) {
                    ForEach(MainTab.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 360)
                Spacer()
                LanguagePicker().frame(width: 130)
            }
            .padding(.top, 4)

            Group {
                switch tab {
                case .contents: UnitsView()
                case .history: HistoryView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .padding(20)
        .onAppear {
            // `--settings` opens the Settings window at launch (handy for screenshots).
            if CommandLine.arguments.contains("--settings") { openSettings() }
        }
        .alert(item: $model.alert) { a in
            Alert(title: Text(a.title), message: Text(a.text), dismissButton: .default(Text(tr("OK", "Понятно"))))
        }
    }
}

struct LanguagePicker: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        Picker(selection: Binding(get: { LangState.shared.lang }, set: { model.setLanguage($0) })) {
            ForEach(Lang.allCases) { Text($0.title).tag($0) }
        } label: {
            Image(systemName: "globe")
        }
        .pickerStyle(.menu)
        .help(tr("Language", "Язык"))
    }
}

// MARK: - Header

struct HeaderView: View {
    @Environment(AppModel.self) private var model

    private var color: Color {
        switch model.freshness {
        case .ok: .green
        case .due: .orange
        case .overdue, .never: .red
        }
    }

    private var title: String {
        guard let d = model.lastSuccess else { return tr("No backups yet", "Копий ещё не было") }
        return tr("Last backup: ", "Последняя копия: ") + "\(Fmt.dateLong(d)) (\(Fmt.relative(d, now: model.now)))"
    }

    private var note: String {
        let disk = model.diskName
        switch model.freshness {
        case .ok:
            return tr("All good: the backup is fresh.", "Всё в порядке: копия свежая.")
        case .due:
            return tr("Time for a new backup: it's been more than \(Fmt.days(model.config.dueDays)).",
                      "Пора сделать новую копию: прошло больше \(model.config.dueDays) \(Fmt.plural(model.config.dueDays, "дня", "дней", "дней")).")
        case .overdue:
            return model.diskMounted
                ? tr("The backup is out of date. Make a new one.", "Копия давно не обновлялась. Сделайте копию.")
                : tr("The backup is out of date. Connect “\(disk)” and make a new one.",
                     "Копия давно не обновлялась. Подключите диск «\(disk)» и сделайте копию.")
        case .never:
            return model.diskMounted
                ? tr("Click “Back Up Now” to make the first backup.", "Нажмите «Сделать копию сейчас», чтобы сделать первую копию.")
                : tr("Connect the backup disk “\(disk)” and click “Back Up Now”.",
                     "Подключите диск «\(disk)» и нажмите «Сделать копию сейчас».")
        }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            Circle()
                .fill(color.gradient)
                .frame(width: 18, height: 18)
                .shadow(color: color.opacity(0.5), radius: 4)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.title2.weight(.semibold))
                Text(note).foregroundStyle(.secondary)
            }
            Spacer(minLength: 16)
            VStack(alignment: .trailing, spacing: 4) {
                if model.paths.isTest && (ProcessInfo.processInfo.environment["MACBACKUP_HIDE_TEST_BADGE"] ?? "").isEmpty {
                    Text(tr("Test mode", "Тестовый режим"))
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(.orange.opacity(0.2), in: Capsule())
                        .foregroundStyle(.orange)
                }
                Label {
                    Text(diskText)
                } icon: {
                    Image(systemName: model.diskMounted ? "externaldrive.fill.badge.checkmark" : "externaldrive.badge.xmark")
                        .foregroundStyle(model.diskMounted ? .green : .secondary)
                }
                .font(.body.weight(.medium))
            }
        }
        .padding(16)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    private var diskText: String {
        let disk = model.diskName
        guard model.diskMounted else { return tr("Disk “\(disk)”: not connected", "Диск «\(disk)»: не подключён") }
        if let f = model.diskFree ?? model.status?.diskFreeBytes {
            return tr("Disk “\(disk)”: connected, \(Fmt.bytes(f)) free", "Диск «\(disk)»: подключён, свободно \(Fmt.bytes(f))")
        }
        return tr("Disk “\(disk)”: connected", "Диск «\(disk)»: подключён")
    }
}

// MARK: - Buttons

struct ActionsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let busy = model.isActive
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                if busy {
                    Button(role: .destructive) { model.cancel() } label: {
                        Label(tr("Stop", "Остановить"), systemImage: "stop.fill")
                    }
                    .controlSize(.large)
                } else {
                    Button { model.startBackup() } label: {
                        Label(tr("Back Up Now", "Сделать копию сейчас"), systemImage: "arrow.clockwise.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(model.startBlockReason != nil)
                }
                Button { model.startVerify() } label: {
                    Label(tr("Verify Archives", "Проверить архивы"), systemImage: "checkmark.shield")
                }
                .controlSize(.large)
                .disabled(model.startBlockReason != nil)
                .help(tr("Read every archive on the disk again and make sure it's intact. Takes a while.",
                         "Заново прочитать все архивы на диске и убедиться, что они целые. Это долго."))

                Spacer()
                Button { model.openBackupFolder() } label: {
                    Label(tr("Show Backup on Disk", "Открыть копию на диске"), systemImage: "folder")
                }
                .controlSize(.large)
                .disabled(!model.diskMounted)
                Button { model.openReadme() } label: {
                    Label(tr("Open README", "Открыть README"), systemImage: "doc.text")
                }
                .controlSize(.large)
                .disabled(!model.diskMounted)
            }
            if let reason = model.startBlockReason, !busy {
                Label(reason, systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - Cards

struct ProgressCard: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let s = model.status ?? Status()
        let verifying = model.liveState == .verifying
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(verifying ? tr("Verifying archives", "Проверяю архивы") : tr("Backing up", "Делаю копию"))
                    .font(.headline)
                if let ph = Fmt.phase(s.phase) {
                    Text("· \(ph)").foregroundStyle(.secondary)
                }
                Spacer()
                Text(Fmt.percent(model.progress))
                    .font(.title3.monospacedDigit().weight(.semibold))
            }
            ProgressView(value: model.progress)
                .progressViewStyle(.linear)
                .controlSize(.large)
            HStack(spacing: 22) {
                stat(tr("Done", "Готово"), tr("\(Fmt.bytes(s.bytesDone)) of \(Fmt.bytes(s.bytesTotal))", "\(Fmt.bytes(s.bytesDone)) из \(Fmt.bytes(s.bytesTotal))"))
                stat(tr("Items", "Папок"), tr("\(s.unitsDone) of \(s.unitsTotal)", "\(s.unitsDone) из \(s.unitsTotal)")
                     + (s.unitsSkipped > 0 ? tr(" (\(s.unitsSkipped) unchanged)", " (\(s.unitsSkipped) без изменений)") : ""))
                stat(tr("Speed", "Скорость"), Fmt.speed(s.speedBps))
                stat(tr("Time", "Время"), Fmt.eta(s.etaSeconds) ?? tr("estimating…", "считаю…"))
                if s.unitsFailed > 0 {
                    stat(tr("Errors", "Ошибок"), "\(s.unitsFailed)").foregroundStyle(.red)
                }
            }
            if let u = s.currentUnit {
                Text(tr("Now: ", "Сейчас: ") + u).font(.callout).lineLimit(1).truncationMode(.middle)
            }
            if let m = s.message {
                Text(m).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .padding(16)
        .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private func stat(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.body.monospacedDigit())
        }
    }
}

struct NoticeCard: View {
    let symbol: String
    let color: Color
    let title: String
    let text: String
    var footnote: String?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(color).font(.title2)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).lineLimit(4)
                if let footnote { Text(footnote).font(.caption).foregroundStyle(.secondary) }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct StalledCard: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        NoticeCard(
            symbol: "exclamationmark.triangle.fill", color: .orange,
            title: tr("The backup was interrupted", "Копия прервалась"),
            text: tr("The backup engine stopped responding (maybe the Mac went to sleep or was shut down). Archives already made are fine. Click “Back Up Now” to continue.",
                     "Программа копирования перестала отвечать (возможно, Mac уснул или выключался). Уже сделанные архивы целы. Нажмите «Сделать копию сейчас», чтобы продолжить."),
            footnote: model.status?.updatedAt.map { tr("Last sign of life: ", "Последний признак жизни: ") + Fmt.dateLong($0) })
    }
}

struct InterruptedCard: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        NoticeCard(
            symbol: "pause.circle.fill", color: .yellow,
            title: tr("Paused", "Пауза"),
            text: tr("Paused — the backup disk was disconnected. It will continue when the disk is connected again.",
                     "Пауза: диск для копий отключили. Копия продолжится, когда диск снова подключат."),
            footnote: (model.status?.pendingSince ?? model.status?.updatedAt).map { tr("Paused since ", "На паузе с ") + Fmt.dateLong($0) })
    }
}

struct ProblemCard: View {
    let run: RunSummary
    var body: some View {
        let failed = run.outcome == .failed
        NoticeCard(
            symbol: failed ? "xmark.octagon.fill" : "stop.circle.fill", color: failed ? .red : .orange,
            title: failed ? tr("The last attempt failed (\(Fmt.dateLong(run.endedAt)))", "Последняя попытка не удалась (\(Fmt.dateLong(run.endedAt)))")
                          : tr("The last backup was stopped (\(Fmt.dateLong(run.endedAt)))", "Последнюю копию остановили (\(Fmt.dateLong(run.endedAt)))"),
            text: failed ? (run.error ?? tr("See the History tab for details.", "Подробности во вкладке «История»."))
                         : tr("Everything done before the stop is kept. You can simply start the backup again.",
                              "Сделанное до остановки сохранено. Можно просто запустить копию ещё раз."))
    }
}

struct AccessBanner: View {
    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: "lock.trianglebadge.exclamationmark.fill").foregroundStyle(.orange).font(.title2)
            VStack(alignment: .leading, spacing: 2) {
                Text(tr("Give MacBackup Full Disk Access: System Settings → Privacy & Security → Full Disk Access",
                        "Дайте MacBackup полный доступ к диску: Системные настройки → Конфиденциальность → Полный доступ к диску"))
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(tr("Without it some files (mail, messages, app settings) can't be backed up.",
                        "Без этого часть файлов (почта, сообщения, настройки) не попадает в копию."))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            Button(tr("Open Settings", "Открыть настройки")) {
                if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") {
                    NSWorkspace.shared.open(url)
                }
            }
            .controlSize(.large)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }
}
