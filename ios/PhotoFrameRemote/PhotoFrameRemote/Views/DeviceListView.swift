import SwiftUI
import UIKit

struct DeviceListView: View {
    @Environment(DeviceDiscovery.self) private var discovery
    @Environment(\.statusBarHeight) private var statusBarHeight

    private var logoTopInset: CGFloat {
        // Place the logo as close as possible to the Dynamic Island without overlap.
        max(statusBarHeight - 36, 6)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Image("FancyFramesLogo")
                            .resizable()
                            .scaledToFit()
                            .frame(maxWidth: .infinity, alignment: .center)
                            .frame(height: 220)
                    }
                    .padding(.horizontal, 12)
                    .padding(.bottom, -34)

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
                .padding(.top, logoTopInset)
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
    @State private var thumbnail: UIImage?

    var body: some View {
        HStack(spacing: 14) {
            avatarView

            VStack(alignment: .leading, spacing: 3) {
                Text(frame.displayName)
                    .font(.headline)
                    .foregroundStyle(frame.isReachable ? .primary : .secondary)

                if let subtitle = hostAndIPSubtitle {
                    Text(subtitle)
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
        .task(id: thumbnailTaskKey) {
            await loadThumbnail()
        }
    }

    private var hostAndIPSubtitle: String? {
        let hostname = frame.hostname?.trimmingCharacters(in: .whitespacesAndNewlines)
        let ip = frame.ipAddress ?? frame.host

        if let hostname, !hostname.isEmpty, let ip, !ip.isEmpty {
            return "\(hostname)  ·  \(ip)"
        }
        if let hostname, !hostname.isEmpty {
            return hostname
        }
        if let ip, !ip.isEmpty {
            return ip
        }
        return nil
    }

    private var avatarView: some View {
        Group {
            if let thumbnail {
                Image(uiImage: thumbnail)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 48, height: 48)
                    .clipShape(Circle())
            } else {
                ZStack {
                    Circle()
                        .fill(frame.isReachable ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.12))
                        .frame(width: 48, height: 48)
                    Image(systemName: "photo.artframe")
                        .font(.title2)
                        .foregroundStyle(frame.isReachable ? Color.accentColor : Color.secondary)
                }
            }
        }
    }

    private var thumbnailTaskKey: String {
        "\(frame.id)|\(frame.host ?? "")|\(frame.isReachable)"
    }

    @MainActor
    private func loadThumbnail() async {
        let frameKey = "\(frame.id)|\(frame.host ?? "")"
        if !FrameRowThumbnailCache.shared.shouldValidate(frameKey: frameKey),
           let meta = FrameRowThumbnailCache.shared.metadata(for: frameKey),
           let cached = FrameRowThumbnailCache.shared.image(forPhotoKey: photoKey(from: meta)) {
            thumbnail = cached
            return
        }

        guard frame.isReachable, let api = frame.api else {
            thumbnail = nil
            return
        }

        do {
            let photos = try await api.fetchPhotoList()
            guard let first = photos.first else {
                FrameRowThumbnailCache.shared.clear(frameKey: frameKey)
                thumbnail = nil
                return
            }

            let meta = FrameRowThumbnailCache.FirstPhotoMeta(
                filename: first.filename,
                version: first.version,
                thumbnailURL: first.thumbnailURL
            )

            if FrameRowThumbnailCache.shared.metadata(for: frameKey) == meta,
               let cached = FrameRowThumbnailCache.shared.image(forPhotoKey: photoKey(from: meta)) {
                FrameRowThumbnailCache.shared.markValidated(frameKey: frameKey)
                thumbnail = cached
                return
            }

            let url = meta.thumbnailURL ?? api.photoURL(
                filename: first.filename,
                version: first.version,
                thumbnail: true
            )

            guard let url else {
                thumbnail = nil
                return
            }

            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode),
                  let image = UIImage(data: data) else {
                thumbnail = nil
                return
            }

            FrameRowThumbnailCache.shared.set(image, forPhotoKey: photoKey(from: meta))
            FrameRowThumbnailCache.shared.update(frameKey: frameKey, meta: meta)
            FrameRowThumbnailCache.shared.markValidated(frameKey: frameKey)
            thumbnail = image
        } catch {
            thumbnail = nil
        }
    }

    private func photoKey(from meta: FrameRowThumbnailCache.FirstPhotoMeta) -> String {
        "\(meta.filename)|\(meta.version)"
    }
}

@MainActor
private final class FrameRowThumbnailCache {
    struct FirstPhotoMeta: Equatable {
        let filename: String
        let version: String
        let thumbnailURL: URL?
    }

    static let shared = FrameRowThumbnailCache()
    private let imageCache = NSCache<NSString, UIImage>()
    private var frameToMeta: [String: FirstPhotoMeta] = [:]
    private var lastValidatedAt: [String: Date] = [:]
    private let validateInterval: TimeInterval = 45

    private init() {}

    func shouldValidate(frameKey: String) -> Bool {
        guard let last = lastValidatedAt[frameKey] else { return true }
        return Date().timeIntervalSince(last) >= validateInterval
    }

    func metadata(for frameKey: String) -> FirstPhotoMeta? {
        frameToMeta[frameKey]
    }

    func image(forPhotoKey key: String) -> UIImage? {
        imageCache.object(forKey: key as NSString)
    }

    func set(_ image: UIImage, forPhotoKey key: String) {
        imageCache.setObject(image, forKey: key as NSString)
    }

    func update(frameKey: String, meta: FirstPhotoMeta) {
        frameToMeta[frameKey] = meta
    }

    func markValidated(frameKey: String) {
        lastValidatedAt[frameKey] = Date()
    }

    func clear(frameKey: String) {
        frameToMeta.removeValue(forKey: frameKey)
        lastValidatedAt.removeValue(forKey: frameKey)
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
