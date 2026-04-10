import SwiftUI

struct DeviceDetailView: View {
    let frame: PhotoFrame

    @State private var config: PhotoFrameConfig?
    @State private var info: PhotoFrameInfo?
    @State private var photoCount: Int?
    @State private var isLoading = true
    @State private var error: String?

    var body: some View {
        List {
            if isLoading {
                Section {
                    HStack {
                        Spacer()
                        ProgressView("Loading…")
                        Spacer()
                    }
                    .padding(.vertical)
                }
            } else {
                // Status row
                Section {
                    HStack(spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            if let info {
                                Label(info.ipAddress ?? frame.host ?? "—", systemImage: "network")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                Label("Up \(info.uptimeFormatted)", systemImage: "clock")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                            Label(photoCount.map { "\($0) photo\($0 == 1 ? "" : "s")" } ?? "— photos",
                                  systemImage: "photo.stack")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        statusBadge
                    }
                    .padding(.vertical, 4)

                    if let config {
                        Text(settingsSummary(config))
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                }

                // Navigation actions
                Section {
                    NavigationLink {
                        PhotosView(frame: frame)
                    } label: {
                        Label("Manage Photos", systemImage: "photo.on.rectangle.angled")
                    }

                    NavigationLink {
                        SettingsView(frame: frame)
                    } label: {
                        Label("Settings", systemImage: "slider.horizontal.3")
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(frame.name)
        .navigationBarTitleDisplayMode(.large)
        .alert("Error", isPresented: .constant(error != nil), actions: {
            Button("OK") { error = nil }
        }, message: {
            Text(error ?? "")
        })
        .task { await load() }
    }

    // MARK: - Status badge

    private var statusBadge: some View {
        Label(
            frame.isReachable ? "Online" : "Unavailable",
            systemImage: "circle.fill"
        )
        .labelStyle(.titleAndIcon)
        .font(.caption)
        .foregroundStyle(frame.isReachable ? .green : .red)
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(
            Capsule().fill(frame.isReachable
                           ? Color.green.opacity(0.12)
                           : Color.red.opacity(0.12))
        )
    }

    // MARK: - Helpers

    private func settingsSummary(_ c: PhotoFrameConfig) -> String {
        let slide = "\(Int(c.slideSeconds))s slides"
        let transition: String
        switch c.transitions.count {
        case 0: transition = "No transitions"
        case 1: transition = c.transitions[0].replacingOccurrences(of: "_", with: " ").capitalized
        case PhotoFrameConfig.default.transitions.count: transition = "All transitions"
        default: transition = "\(c.transitions.count) transitions"
        }
        let kb = c.kenBurns ? "Ken Burns on" : "Ken Burns off"
        return "\(slide)  ·  \(transition)  ·  \(kb)"
    }

    // MARK: - Network

    private func load() async {
        guard let api = frame.api else {
            isLoading = false
            error = "Frame is not reachable."
            return
        }
        do {
            async let fetchedConfig = api.fetchConfig()
            async let fetchedInfo   = api.fetchInfo()
            async let fetchedCount  = api.fetchPhotoCount()
            let (c, i, n) = try await (fetchedConfig, fetchedInfo, fetchedCount)
            config     = c
            info       = i
            photoCount = n
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
