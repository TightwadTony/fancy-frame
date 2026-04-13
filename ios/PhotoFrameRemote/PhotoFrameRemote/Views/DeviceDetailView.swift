import SwiftUI

struct DeviceDetailView: View {
    let frame: PhotoFrame
    @Environment(\.statusBarHeight) private var statusBarHeight
    @Environment(\.dismiss) private var dismiss

    @State private var config: PhotoFrameConfig?
    @State private var info: PhotoFrameInfo?
    @State private var photoCount: Int?
    @State private var isLoading = true
    @State private var error: String?

    private let refreshTimer = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                // Back button header
                HStack {
                    Button { dismiss() } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "chevron.left")
                                .fontWeight(.semibold)
                            Text("Back")
                        }
                        .font(.body)
                    }
                    Spacer()
                    Text(frame.displayName)
                        .font(.headline)
                    Spacer()
                    // balance chevron width
                    Color.clear.frame(width: 60, height: 1)
                }
                .padding(.horizontal, 12)

                if isLoading {
                    HStack {
                        Spacer()
                        ProgressView("Loading…")
                        Spacer()
                    }
                    .padding(.vertical, 20)
                } else {
                    VStack(alignment: .leading, spacing: 10) {
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

                        if let config {
                            Text(settingsSummary(config))
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                    }
                    .padding(14)
                    .background(Color(.secondarySystemGroupedBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 16))

                    VStack(spacing: 10) {
                        NavigationLink {
                            PhotosView(frame: frame)
                        } label: {
                            Label("Manage Photos", systemImage: "photo.on.rectangle.angled")
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 12)
                                .background(Color(.secondarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 16))
                        }
                        .buttonStyle(.plain)

                        NavigationLink {
                            SettingsView(frame: frame)
                        } label: {
                            Label("Settings", systemImage: "slider.horizontal.3")
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 12)
                                .background(Color(.secondarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 16))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(.top, statusBarHeight + 8)
            .padding(.horizontal, 12)
            .padding(.bottom, 12)
        }
        .ignoresSafeArea(edges: .top)
        .toolbar(.hidden, for: .navigationBar)
        .fancyFrameScreenBackground()
        .alert("Error", isPresented: .constant(error != nil), actions: {
            Button("OK") { error = nil }
        }, message: {
            Text(error ?? "")
        })
        .task { await load() }
        .onAppear {
            guard !isLoading else { return }
            Task { await refresh() }
        }
        .onReceive(refreshTimer) { _ in
            Task { await refresh() }
        }
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
            frame.updateDisplayName(c.frameName)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    private func refresh() async {
        guard let api = frame.api else { return }
        do {
            async let fetchedConfig = api.fetchConfig()
            async let fetchedInfo   = api.fetchInfo()
            async let fetchedCount  = api.fetchPhotoCount()
            let (c, i, n) = try await (fetchedConfig, fetchedInfo, fetchedCount)
            config     = c
            info       = i
            photoCount = n
            frame.updateDisplayName(c.frameName)
        } catch {
            // Suppress errors on background refresh — don't interrupt the user
        }
    }
}
