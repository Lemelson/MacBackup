import SwiftUI

struct UnitsView: View {
    @Environment(AppModel.self) private var model
    @State private var filter: Layer?
    @State private var sortOrder = [KeyPathComparator(\UnitInfo.bytesRaw, order: .reverse)]
    @State private var selection: UnitInfo.ID?

    private struct Totals { var count = 0; var raw = 0.0; var archive = 0.0; var failed = 0 }

    private func totals(_ units: [UnitInfo]) -> Totals {
        units.reduce(into: Totals()) { t, u in
            t.count += 1; t.raw += u.bytesRaw; t.archive += u.bytesArchive
            if u.verifyOK == false { t.failed += 1 }
        }
    }

    var body: some View {
        let all = model.index?.units ?? []
        if all.isEmpty {
            ContentUnavailableView {
                Label(tr("No backups yet", "Копий ещё не было"), systemImage: "tray")
            } description: {
                Text(model.loadedOnce ? tr("After the first backup, everything it contains will be listed here.",
                                         "Здесь появится список всего, что лежит в копии, после первой копии.")
                                      : tr("Loading…", "Загружаю…"))
            }
        } else {
            let layers = Layer.allCases.filter { l in all.contains { $0.layer == l } }
            let rows = all.filter { filter == nil || $0.layer == filter }.sorted(using: sortOrder)
            VStack(alignment: .leading, spacing: 10) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 8) {
                        chip(nil, totals(all))
                        ForEach(layers) { l in chip(l, totals(all.filter { $0.layer == l })) }
                    }
                }
                table(rows)
                HStack {
                    let t = totals(rows)
                    Text("\(filter?.title ?? tr("Total", "Всего")): " + tr("\(t.count) items, \(Fmt.bytes(t.raw)) → \(Fmt.bytes(t.archive)) on disk",
                                                                   "\(t.count) шт., \(Fmt.bytes(t.raw)) → \(Fmt.bytes(t.archive)) в копии"))
                    Spacer()
                    if let idx = model.index {
                        Text((idx.fromDisk ? tr("List from disk “\(model.diskName)”", "Список с диска «\(model.diskName)»")
                                           : tr("List saved on this Mac (disk not connected)", "Список с Mac (диск не подключён)"))
                             + (idx.updatedAt.map { tr(", updated ", ", обновлён ") + Fmt.dateLong($0) } ?? ""))
                    }
                }
                .font(.callout)
                .foregroundStyle(.secondary)
            }
        }
    }

    private func chip(_ layer: Layer?, _ t: Totals) -> some View {
        let selected = filter == layer
        return Button {
            filter = layer
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Label(layer?.title ?? tr("All", "Всё"), systemImage: layer?.symbol ?? "square.stack.3d.up.fill")
                    .font(.callout.weight(.semibold))
                Text(tr("\(t.count) items", "\(t.count) шт.") + " · \(Fmt.bytes(t.raw)) → \(Fmt.bytes(t.archive))")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                if t.failed > 0 {
                    Text(tr("failed verification: \(t.failed)", "не прошли проверку: \(t.failed)")).font(.caption).foregroundStyle(.red)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .frame(minWidth: 150, minHeight: 62, alignment: .topLeading)
            .background(selected ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.08),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .strokeBorder(selected ? Color.accentColor.opacity(0.6) : .clear, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
    }

    private func table(_ rows: [UnitInfo]) -> some View {
        Table(rows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn(tr("Name", "Название"), value: \.title) { u in
                Text(u.title).help(u.source.isEmpty ? u.id : u.source)
            }
            .width(min: 150, ideal: 220)
            TableColumn(tr("Category", "Раздел"), value: \.layerSort) { u in
                Label(u.layer.title, systemImage: u.layer.symbol).foregroundStyle(.secondary)
            }
            .width(min: 110, ideal: 160)
            TableColumn(tr("Original", "Было"), value: \.bytesRaw) { u in
                Text(Fmt.bytes(u.bytesRaw)).monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 70, ideal: 85)
            TableColumn(tr("In backup", "В копии"), value: \.bytesArchive) { u in
                Group {
                    if u.isPlainFolder {
                        Text(tr("folder, not compressed", "папка, без сжатия")).foregroundStyle(.secondary)
                            .help(tr("Media is copied to the disk as a plain folder: ", "Медиа копируется на диск обычной папкой: ") + u.archive)
                    } else {
                        Text(Fmt.bytes(u.bytesArchive)).monospacedDigit()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 90, ideal: 150)
            TableColumn(tr("Compression", "Сжатие"), value: \.ratio) { u in
                Text(Fmt.ratio(u.ratio)).monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 70, ideal: 90)
            TableColumn(tr("Updated", "Обновлено"), value: \.createdSort) { u in
                Text(Fmt.dateShort(u.createdAt)).monospacedDigit()
            }
            .width(min: 100, ideal: 120)
            TableColumn(tr("Verified", "Проверено"), value: \.verifySort) { u in
                verifyCell(u)
            }
            .width(min: 80, ideal: 100)
        }
    }

    @ViewBuilder
    private func verifyCell(_ u: UnitInfo) -> some View {
        switch u.verifyOK {
        case true?:
            Label("✓", systemImage: "checkmark.circle.fill").labelStyle(.iconOnly).foregroundStyle(.green)
                .help(tr("Verified \(Fmt.dateLong(u.verifiedAt)): archive is intact", "Проверено \(Fmt.dateLong(u.verifiedAt)): архив целый"))
        case false?:
            HStack(spacing: 4) {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                Text(tr("error", "ошибка")).foregroundStyle(.red)
            }
            .help(tr("The archive failed verification on \(Fmt.dateLong(u.verifiedAt)). Make a new backup.", "Архив не прошёл проверку \(Fmt.dateLong(u.verifiedAt)). Сделайте копию ещё раз."))
        case nil:
            Text("—").foregroundStyle(.secondary).help(tr("Not verified yet", "Ещё не проверялся"))
        }
    }
}
