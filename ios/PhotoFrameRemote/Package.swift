// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "FancyFrame",
    platforms: [.iOS(.v17)],
    products: [
        .executable(
            name: "FancyFrame",
            targets: ["FancyFrame"]
        )
    ],
    targets: [
        .executableTarget(
            name: "FancyFrame",
            path: "PhotoFrameRemote"
        )
    ]
)
