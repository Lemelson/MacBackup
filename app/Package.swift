// swift-tools-version:6.2
import PackageDescription

let package = Package(
    name: "MacBackup",
    defaultLocalization: "ru",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "MacBackup",
            path: "Sources/MacBackup",
            swiftSettings: [.defaultIsolation(MainActor.self)]
        )
    ]
)
