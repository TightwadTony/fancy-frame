// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PhotoFrameRemote",
    platforms: [.iOS(.v17)],
    targets: [
        .executableTarget(
            name: "PhotoFrameRemote",
            path: "PhotoFrameRemote"
        )
    ]
)
