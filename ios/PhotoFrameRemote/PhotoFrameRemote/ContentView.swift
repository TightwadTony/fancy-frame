import SwiftUI

struct ContentView: View {
    var body: some View {
        TabView {
            DeviceListView()
                .tabItem {
                    Label("Frames", systemImage: "antenna.radiowaves.left.and.right")
                }
        }
    }
}
