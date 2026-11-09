import AppKit
import SwiftUI

/// The one model shared by the window, the menu bar item and the quit check.
/// Created lazily, so it never exists in the headless --auto / --notify modes.
let sharedModel = AppModel(paths: AppPaths.resolve())

@main
enum Entry {
    static func main() {
        let args = CommandLine.arguments
        if args.contains("--auto") {
            exit(AutoRunner.run(paths: AppPaths.resolve()))
        }
        if let i = args.firstIndex(of: "--notify") {
            let title = args.count > i + 1 ? args[i + 1] : "MacBackup"
            let body = args.count > i + 2 ? args[i + 2] : ""
            exit(Notifier.post(title: title, body: body))
        }
        MacBackupApp.main()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // MACBACKUP_APPEARANCE=light|dark forces the look (used for README screenshots).
        switch ProcessInfo.processInfo.environment["MACBACKUP_APPEARANCE"] {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark": NSApp.appearance = NSAppearance(named: .darkAqua)
        default: break
        }
        AppModel.requestNotificationPermission()
    }

    /// Closing the window keeps the app (and a running backup) alive in the menu bar.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let model = sharedModel
        guard model.hasRunningChild || model.isActive else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = tr("A backup is still running", "Копия ещё идёт")
        alert.informativeText = tr(
            "If you quit MacBackup, the backup stops (what's done so far is kept). You can just close the window instead: MacBackup stays in the menu bar and finishes the backup.",
            "Если выйти из MacBackup, копия остановится (уже сделанное сохранится). Окно можно просто закрыть: MacBackup останется в строке меню и спокойно доделает копию.")
        alert.addButton(withTitle: tr("Don't Quit", "Не выходить"))
        alert.addButton(withTitle: tr("Stop Backup and Quit", "Остановить копию и выйти"))
        NSApp.activate()
        guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
        model.stopAndWait(timeout: 20)
        return .terminateNow
    }
}

struct MacBackupApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var model = sharedModel

    var body: some Scene {
        Window("MacBackup", id: "main") {
            ContentView()
                .environment(model)
                .environment(\.locale, LangState.shared.lang.locale)
                .frame(minWidth: 960, minHeight: 680)
        }
        .defaultSize(width: 1120, height: 820)

        Settings {
            SettingsView()
                .environment(model)
                .environment(\.locale, LangState.shared.lang.locale)
        }

        MenuBarExtra {
            MenuContent().environment(model)
        } label: {
            MenuLabel(model: model)
        }
        .menuBarExtraStyle(.menu)
    }
}

struct MenuLabel: View {
    let model: AppModel
    var body: some View {
        switch model.liveState {
        case .running, .verifying:
            HStack(spacing: 3) {
                Image(systemName: "arrow.triangle.2.circlepath")
                Text(Fmt.percent(model.progress))
            }
        case .interrupted:
            Image(systemName: "pause.circle")
        case .stalled:
            Image(systemName: "externaldrive.badge.xmark")
        case .idle:
            if model.lastProblem?.outcome == .failed || model.freshness == .overdue || model.freshness == .never {
                Image(systemName: "externaldrive.badge.xmark")
            } else if model.freshness == .due {
                Image(systemName: "externaldrive.badge.exclamationmark")
            } else {
                Image(systemName: "externaldrive.badge.checkmark")
            }
        }
    }
}

struct MenuContent: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        switch model.liveState {
        case .running:
            Text(tr("Backing up: ", "Делаю копию: ") + Fmt.percent(model.progress))
            if let eta = Fmt.eta(model.status?.etaSeconds) { Text(eta) }
        case .verifying:
            Text(tr("Verifying archives: ", "Проверяю архивы: ") + Fmt.percent(model.progress))
        case .interrupted:
            Text(tr("Paused until the backup disk is connected", "Пауза до подключения диска"))
        case .stalled:
            Text(tr("The backup was interrupted", "Копия прервалась"))
        case .idle:
            if let d = model.lastSuccess {
                Text(tr("Last backup: ", "Последняя копия: ") + Fmt.relative(d, now: model.now))
            } else {
                Text(tr("No backups yet", "Копий ещё не было"))
            }
        }
        Text(model.diskMounted ? tr("Disk “\(model.diskName)” connected", "Диск «\(model.diskName)» подключён")
                               : tr("Disk “\(model.diskName)” not connected", "Диск «\(model.diskName)» не подключён"))
        Divider()
        if model.isActive {
            Button(tr("Stop", "Остановить")) { model.cancel() }
        } else {
            Button(tr("Back Up Now", "Сделать копию сейчас")) { model.startBackup() }
                .disabled(model.startBlockReason != nil)
        }
        Button(tr("Open MacBackup", "Открыть MacBackup")) {
            openWindow(id: "main")
            NSApp.activate()
        }
        SettingsLink { Text(tr("Settings…", "Настройки…")) }
        Divider()
        Button(tr("Quit", "Выйти")) { NSApp.terminate(nil) }
    }
}
