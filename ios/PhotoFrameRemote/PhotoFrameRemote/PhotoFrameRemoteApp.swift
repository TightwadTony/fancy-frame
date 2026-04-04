import SwiftUI

@main
struct PhotoFrameRemoteApp: App {
    @State private var discovery = DeviceDiscovery()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(discovery)
        }
    }
}
