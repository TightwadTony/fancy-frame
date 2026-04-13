import SwiftUI

@main
struct FancyFrameApp: App {
    @State private var discovery = DeviceDiscovery()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(discovery)
        }
    }
}
