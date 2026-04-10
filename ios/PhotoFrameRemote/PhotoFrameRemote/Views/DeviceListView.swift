import SwiftUI

struct DeviceListView: View {
    @Environment(DeviceDiscovery.self) private var discovery

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack(alignment: .center) {
                    Text("Photo Frames")
                        .font(.largeTitle.bold())
                    Spacer()
                    if discovery.isSearching {
                        ProgressView()
                            .scaleEffect(0.35)
                            .frame(width: 20, height: 20)
                    } else {
                        Button {
                            discovery.rescan()
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                }
                .padding(.horizontal)
                .padding(.bottom, 4)
                .background(Color(.systemGroupedBackground))

                if discovery.frames.isEmpty {
                    EmptyStateView(isSearching: discovery.isSearching) {
                        discovery.rescan()
                    }
                } else {
                    List(discovery.frames) { frame in
                        NavigationLink {
                            DeviceDetailView(frame: frame)
                        } label: {
                            FrameRowView(frame: frame)
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationBarHidden(true)
        }
    }
}

// MARK: - Frame Row

private struct FrameRowView: View {
    let frame: PhotoFrame

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(frame.isReachable ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.12))
                    .frame(width: 48, height: 48)
                Image(systemName: "photo.artframe")
                    .font(.title2)
                    .foregroundStyle(frame.isReachable ? Color.accentColor : Color.secondary)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(frame.name)
                    .font(.headline)
                    .foregroundStyle(frame.isReachable ? .primary : .secondary)

                if let host = frame.host {
                    Text("\(host)  ·  \(frame.host ?? "—")")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                HStack(spacing: 4) {
                    Circle()
                        .fill(frame.isReachable ? Color.green : Color.red)
                        .frame(width: 7, height: 7)
                    Text(frame.isReachable ? "Online" : "Unavailable")
                        .font(.caption)
                        .foregroundStyle(frame.isReachable ? .green : .red)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Empty State

private struct EmptyStateView: View {
    let isSearching: Bool
    let onRescan: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "antenna.radiowaves.left.and.right")
                .font(.system(size: 64))
                .foregroundStyle(.quaternary)

            Text("No Frames Found")
                .font(.title2.bold())

            Text("Make sure your iPhone and photo frames\nare on the same Wi-Fi network.\nFrames must not be in setup mode.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            if isSearching {
                ProgressView()
                    .padding(.top, 4)
            } else {
                Button("Scan Again", action: onRescan)
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .padding(.top, 4)
            }
            Spacer()
        }
        .padding(.horizontal, 40)
    }
}
