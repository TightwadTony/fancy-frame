// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PhotoFrameRemote",
    platforms: [.iOS(.v18)],
    products: [
        .executable(
            name: "PhotoFrameRemote",
            targets: ["PhotoFrameRemote"]
        )
    ],
    targets: [
        .executableTarget(
            name: "PhotoFrameRemote",
            path: "PhotoFrameRemote"
        )
    ]
)
