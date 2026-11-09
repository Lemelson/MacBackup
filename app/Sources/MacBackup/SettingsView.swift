import AppKit
import SwiftUI

/// Settings window (⌘,). Everything is saved to config.json right away.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var volumes: [String] = []

    var body: some View {
        Form {
            Picker(tr("Language", "Язык"), selection: Binding(get: { LangState.shared.lang }, set: { model.setLanguage($0) })) {
                ForEach(Lang.allCases) { Text($0.title).tag($0) }
            }

            Picker(tr("Backup disk", "Диск для копий"),
                   selection: Binding(get: { model.config.diskVolume }, set: { model.setDiskVolume($0) })) {
                ForEach(volumeChoices, id: \.self) { path in
                    Text(label(for: path)).tag(path)
                }
            }
            Text(tr("Backups go to the “\(model.config.destFolder)” folder on this disk.",
                    "Копии лежат в папке «\(model.config.destFolder)» на этом диске."))
                .font(.caption).foregroundStyle(.secondary)

            Stepper(value: Binding(get: { model.config.dueDays }, set: { model.setDueDays($0) }), in: 1...90) {
                Text(tr("Back up every ", "Делать копию каждые ") + Fmt.days(model.config.dueDays))
            }
            Stepper(value: Binding(get: { model.config.graceDays }, set: { model.setGraceDays($0) }), in: 0...30) {
                let n = model.config.graceDays
                Text(tr("Remind me after \(n) more \(n == 1 ? "day" : "days")", "Напомнить ещё через \(Fmt.days(n))"))
            }

            LabeledContent(tr("Data folder", "Папка с данными")) {
                HStack {
                    Text(model.paths.data.path)
                        .font(.callout.monospaced())
                        .lineLimit(1).truncationMode(.middle)
                        .textSelection(.enabled)
                    Button(tr("Reveal", "Показать")) {
                        let url = model.paths.data
                        if FileManager.default.fileExists(atPath: url.path) {
                            NSWorkspace.shared.activateFileViewerSelecting([url])
                        } else {
                            NSWorkspace.shared.open(url.deletingLastPathComponent())
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 560)
        .fixedSize(horizontal: false, vertical: true)
        .onAppear { volumes = Self.externalVolumes() }
    }

    private var volumeChoices: [String] {
        var all = volumes
        if !all.contains(model.config.diskVolume) { all.insert(model.config.diskVolume, at: 0) }
        return all
    }

    private func label(for path: String) -> String {
        let name = URL(filePath: path).lastPathComponent
        return volumes.contains(path) ? name : name + tr(" (not connected)", " (не подключён)")
    }

    /// Mounted volumes that are not the startup disk (external, removable or ejectable).
    static func externalVolumes() -> [String] {
        let keys: [URLResourceKey] = [.volumeIsInternalKey, .volumeIsRemovableKey, .volumeIsEjectableKey, .volumeIsRootFileSystemKey]
        let urls = FileManager.default.mountedVolumeURLs(includingResourceValuesForKeys: keys, options: [.skipHiddenVolumes]) ?? []
        return urls.compactMap { url -> String? in
            guard url.path.hasPrefix("/Volumes/"), let v = try? url.resourceValues(forKeys: Set(keys)) else { return nil }
            if v.volumeIsRootFileSystem == true { return nil }
            let external = v.volumeIsInternal == false || v.volumeIsRemovable == true || v.volumeIsEjectable == true
            return external ? url.path : nil
        }
        .sorted()
    }
}
