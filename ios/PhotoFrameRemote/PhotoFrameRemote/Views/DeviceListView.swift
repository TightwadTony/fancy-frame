import SwiftUI

struct DeviceListView: View {
    @Environment(DeviceDiscovery.self) private var discovery
    @Environment(\.statusBarHeight) private var statusBarHeight

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .center) {
                        Text("FancyFrames")
                            .font(.custom("Snell Roundhand", size: 32).weight(.bold))
                        Spacer()
                        Image("AppIconImage")
                            .resizable()
                            .scaledToFill()
                            .frame(width: 44, height: 44)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .padding(.horizontal, 12)

                    if discovery.frames.isEmpty {
                        EmptyStateView()
                    } else {
                        LazyVStack(spacing: 10) {
                            ForEach(discovery.frames) { frame in
                                NavigationLink {
                                    DeviceDetailView(frame: frame)
                                } label: {
                                    FrameRowView(frame: frame)
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 12)
                                        .background(Color(.secondarySystemGroupedBackground))
                                        .clipShape(RoundedRectangle(cornerRadius: 18))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 12)
                    }
                }
                .padding(.top, statusBarHeight + 8)
                .padding(.bottom, 12)
            }
            .ignoresSafeArea(edges: .top)
            .refreshable {
                discovery.rescan()
            }
            .toolbar(.hidden, for: .navigationBar)
            .fancyFrameScreenBackground()
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
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "antenna.radiowaves.left.and.right")
                .font(.system(size: 64))
                .foregroundStyle(.quaternary)

            Text("No FancyFrames Found")
                .font(.title2.bold())

            Text("Make sure your iPhone and FancyFrames\nare on the same Wi-Fi network.\nFancyFrames must not be in setup mode.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()
        }
        .padding(.horizontal, 40)
        .padding(.top, 24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}
