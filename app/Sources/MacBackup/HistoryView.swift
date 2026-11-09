import SwiftUI

struct HistoryView: View {
    @Environment(AppModel.self) private var model
    @State private var selection: RunSummary.ID?

    var body: some View {
        if model.runs.isEmpty {
            ContentUnavailableView {
                Label(tr("No backups yet", "Копий ещё не было"), systemImage: "clock.arrow.circlepath")
            } description: {
                Text(tr("Every backup will be listed here: when it ran, how long it took and how it ended.", "Здесь будет список всех копий: когда, сколько длилась и чем закончилась."))
            }
        } else {
            HSplitView {
                List(model.runs, selection: $selection) { run in
                    RunRow(run: run).tag(run.id)
                }
                .frame(minWidth: 380, idealWidth: 440)

                Group {
                    if let id = selection, let run = model.runs.first(where: { $0.id == id }) {
                        RunDetail(run: run)
                    } else {
                        ContentUnavailableView(tr("Select a backup on the left", "Выберите копию слева"), systemImage: "hand.point.left",
                                               description: Text(tr("Details and the log will appear here.", "Покажу подробности и журнал.")))
                    }
                }
                .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
            }
            .onAppear { if selection == nil { selection = model.runs.first?.id } }
        }
    }
}

struct OutcomeBadge: View {
    let outcome: RunOutcome
    var body: some View {
        let (text, color): (String, Color) = switch outcome {
        case .done: (tr("Done", "Готово"), .green)
        case .failed: (tr("Failed", "Ошибка"), .red)
        case .cancelled: (tr("Stopped", "Остановлено"), .orange)
        }
        Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }
}

struct RunRow: View {
    let run: RunSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(Fmt.dateLong(run.startedAt ?? run.endedAt)).font(.body.weight(.semibold))
                Spacer()
                OutcomeBadge(outcome: run.outcome)
            }
            Text([Fmt.mode(run.mode), Fmt.duration(run.duration), countsLine(run)].filter { $0 != "—" && !$0.isEmpty }
                .joined(separator: " · "))
                .font(.callout).foregroundStyle(.secondary)
            if let raw = run.bytesRaw {
                Text("\(Fmt.bytes(raw)) → \(Fmt.bytes(run.bytesArchive))"
                     + (run.averageSpeed.map { tr(" · avg ", " · в среднем ") + Fmt.speed($0) } ?? ""))
                    .font(.callout.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

func countsLine(_ r: RunSummary) -> String {
    // units_done = archived (changed) only; skipped ones are counted separately.
    let failed = r.unitsFailed ?? (r.failures.isEmpty ? nil : r.failures.count)
    guard r.unitsDone != nil || r.unitsSkipped != nil || failed != nil else { return "" }
    return tr("updated \(r.unitsDone ?? 0), unchanged \(r.unitsSkipped ?? 0), errors \(failed ?? 0)",
              "обновлено \(r.unitsDone ?? 0), без изменений \(r.unitsSkipped ?? 0), ошибок \(failed ?? 0)")
}

struct RunDetail: View {
    @Environment(AppModel.self) private var model
    let run: RunSummary
    @State private var log: String?
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(tr("Backup of ", "Копия ") + Fmt.dateLong(run.startedAt ?? run.endedAt)).font(.title3.weight(.semibold))
                OutcomeBadge(outcome: run.outcome)
                Spacer()
                Button(tr("Open Full Log", "Открыть журнал целиком")) { model.openLog(run.id) }
                    .disabled(log == nil)
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                row(tr("Started", "Запуск"), Fmt.mode(run.mode))
                row(tr("Duration", "Длилась"), Fmt.duration(run.duration))
                row(tr("Items", "Папок"), countsLine(run).isEmpty ? "—" : countsLine(run))
                row(tr("Size", "Объём"), run.bytesRaw == nil ? "—" : "\(Fmt.bytes(run.bytesRaw)) → \(Fmt.bytes(run.bytesArchive))" + tr(" on disk", " в копии"))
                row(tr("Speed", "Скорость"), Fmt.speed(run.averageSpeed))
            }
            .font(.callout)

            if run.outcome == .failed, let e = run.error {
                Text(e).foregroundStyle(.red).textSelection(.enabled)
            }
            if !run.failures.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(tr("Couldn't back up:", "Не получилось скопировать:")).font(.headline).foregroundStyle(.red)
                    ForEach(run.failures) { f in
                        HStack(alignment: .firstTextBaseline) {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                            Text(f.unit).fontWeight(.medium)
                            Text(f.error ?? (f.isVerify ? tr("archive failed verification", "архив не прошёл проверку") : tr("unknown error", "неизвестная ошибка")))
                                .foregroundStyle(.secondary).textSelection(.enabled)
                        }
                        .font(.callout)
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.red.opacity(0.07), in: RoundedRectangle(cornerRadius: 8))
            }

            Text(tr("Log", "Журнал")).font(.headline)
            ScrollView([.vertical, .horizontal]) {
                Text(log ?? (loaded ? tr("There is no log for this backup.", "Журнала этой копии нет.") : tr("Loading…", "Загружаю…")))
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding(10)
            }
            .defaultScrollAnchor(.bottomLeading)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(.leading, 14)
        .task(id: run.id) {
            loaded = false
            log = await Loader.logTail(model.paths.runLog(run.id))
            loaded = true
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        GridRow {
            Text(k).foregroundStyle(.secondary)
            Text(v)
        }
    }
}
