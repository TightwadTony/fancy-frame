import SwiftUI
import PhotosUI
import UIKit

struct PhotosView: View {
    let frame: PhotoFrame
    @Environment(\.statusBarHeight) private var statusBarHeight
    @Environment(\.dismiss) private var dismiss

    @State private var photoCount: Int?
    @State private var isLoading = true
    @State private var uploadPreference: UploadSizePreset
    @State private var showPicker = false
    @State private var uploads: [UploadItem] = []
    @State private var isUploading = false
    @State private var photos: [PhotoFramePhoto] = []
    @State private var selectedPhotos = Set<String>()
    @State private var isDeleting = false
    @State private var showDeleteConfirmation = false
    @State private var error: String?
    @StateObject private var thumbnailCache = ThumbnailCache()

    init(frame: PhotoFrame) {
        self.frame = frame
        _uploadPreference = State(initialValue: FrameUploadPreferenceStore.load(for: frame))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                // Back button + title + upload button header
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
                    Text("Photos")
                        .font(.headline)
                    Spacer()
                    Button {
                        showPicker = true
                    } label: {
                        if isUploading {
                            ProgressView().scaleEffect(0.85)
                        } else {
                            Image(systemName: "plus")
                                .font(.body.weight(.semibold))
                        }
                    }
                    .disabled(isUploading || isDeleting)
                }
                .padding(.horizontal, 12)

                compactInfoCard

                if !photos.isEmpty {
                    LazyVGrid(
                        columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: 3),
                        spacing: 0
                    ) {
                        ForEach(photos) { photo in
                            PhotoThumbnailCell(
                                photo: photo,
                                api: frame.api,
                                cache: thumbnailCache,
                                isSelected: selectedPhotos.contains(photo.filename),
                                selectionAction: { toggleSelection(photo.filename) }
                            )
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                Button(role: .destructive) {
                                    selectedPhotos = [photo.filename]
                                    showDeleteConfirmation = true
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                } else if !isLoading {
                    Text("No photos uploaded to the frame yet.")
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 4)
                }

                if !uploads.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Uploads")
                            .font(.headline)
                        ForEach(uploads) { item in
                            UploadRowView(item: item)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 10)
                                .background(Color(.secondarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                    }
                }
            }
        }
        .contentMargins(.top, statusBarHeight + 8, for: .scrollContent)
        .contentMargins(.bottom, selectedPhotos.isEmpty ? 8 : 70, for: .scrollContent)
        .contentMargins(.leading, 12, for: .scrollContent)
        .contentMargins(.trailing, 12, for: .scrollContent)
        .ignoresSafeArea(edges: .top)
        .toolbar(.hidden, for: .navigationBar)
        .fancyFrameScreenBackground()
        .sheet(isPresented: $showPicker) {
            PhotoPicker { selections in
                showPicker = false
                guard !selections.isEmpty else { return }
                startUploads(selections)
            }
        }
        .alert("Error", isPresented: .constant(error != nil), actions: {
            Button("OK") { error = nil }
        }, message: {
            Text(error ?? "")
        })
        .alert("Delete photos?", isPresented: $showDeleteConfirmation) {
            Button("Delete", role: .destructive) {
                Task { await deleteSelectedPhotos() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will permanently remove \(selectedPhotos.count) photo\(selectedPhotos.count == 1 ? "" : "s") from the frame.")
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !selectedPhotos.isEmpty {
                bottomDeleteBar
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
            }
        }
        .task { await loadPhotos() }
        .onChange(of: uploadPreference) { _, newValue in
            FrameUploadPreferenceStore.save(newValue, for: frame)
        }
    }

    private var compactInfoCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                if isLoading {
                    ProgressView()
                        .scaleEffect(0.7)
                } else {
                    Image(systemName: "photo.stack")
                        .foregroundStyle(.secondary)
                    Text(photoCount.map { "\($0) photo\($0 == 1 ? "" : "s") on frame" } ?? "Unknown")
                        .foregroundStyle(.secondary)
                }
            }

            Divider()

            HStack(spacing: 10) {
                Label("Upload Size", systemImage: "arrow.up.circle")
                    .foregroundStyle(.secondary)

                Spacer()

                Menu {
                    ForEach(UploadSizePreset.allCases) { preset in
                        Button {
                            uploadPreference = preset
                        } label: {
                            if preset == uploadPreference {
                                Label(preset.menuTitle, systemImage: "checkmark")
                            } else {
                                Text(preset.menuTitle)
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(uploadPreference.shortTitle)
                            .fontWeight(.semibold)
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption2)
                    }
                    .foregroundStyle(.tint)
                }
            }

            Text("Uploads for this frame are resized to \(uploadPreference.descriptionText) at 85% JPEG quality.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private var bottomDeleteBar: some View {
        HStack {
            Spacer()
            Button(role: .destructive) {
                showDeleteConfirmation = true
            } label: {
                Label("Delete (\(selectedPhotos.count))", systemImage: "trash")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
            }
            .buttonStyle(.plain)
            .background(Color.red.opacity(0.78))
            .foregroundStyle(.white)
            .clipShape(Capsule())
            .disabled(isDeleting || isUploading)
            Spacer()
        }
        .padding(.vertical, 6)
        .background(.ultraThinMaterial.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
    }

    // MARK: - Network

    private func loadPhotos() async {
        guard let api = frame.api else {
            isLoading = false
            error = "Frame not reachable"
            return
        }

        isLoading = true
        do {
            photos = try await api.fetchPhotoList()
            photoCount = photos.count
        } catch let apiError {
            self.error = apiError.localizedDescription
        }
        isLoading = false
    }

    private func startUploads(_ selections: [PhotoSelection]) {
        let newItems = selections.map { UploadItem(name: $0.suggestedFilename) }
        let uploadPreset = uploadPreference
        uploads.append(contentsOf: newItems)
        isUploading = true

        Task {
            for (index, selection) in selections.enumerated() {
                let item = newItems[index]
                await uploadOne(selection: selection, item: item, preset: uploadPreset)
            }
            isUploading = false
            await loadPhotos()
        }
    }

    private func uploadOne(selection: PhotoSelection, item: UploadItem, preset: UploadSizePreset) async {
        guard let api = frame.api else {
            item.state = .failed("Frame not reachable")
            return
        }
        item.state = .uploading

        do {
            let (jpegData, filename) = try await selection.loadAsJPEG(
                maxPixelSize: preset.maximumPixelSize,
                compressionQuality: preset.compressionQuality
            )
            try await api.uploadPhoto(jpegData, filename: filename)
            item.state = .done
        } catch let uploadError {
            item.state = .failed(uploadError.localizedDescription)
        }
    }

    private func deleteSelectedPhotos() async {
        guard !selectedPhotos.isEmpty, let api = frame.api else { return }
        isDeleting = true
        let filenames = Array(selectedPhotos)
        var errors: [String] = []

        for filename in filenames {
            do {
                try await api.deletePhoto(filename: filename)
            } catch let deleteError {
                errors.append("\(filename): \(deleteError.localizedDescription)")
            }
        }

        selectedPhotos.removeAll()
        await loadPhotos()
        isDeleting = false

        if !errors.isEmpty {
            error = errors.joined(separator: "\n")
        }
    }

    private func toggleSelection(_ filename: String) {
        if selectedPhotos.contains(filename) {
            selectedPhotos.remove(filename)
        } else {
            selectedPhotos.insert(filename)
        }
    }
}

// MARK: - Thumbnail caching

private final class ThumbnailCache: ObservableObject {
    private let memoryCache = NSCache<NSString, UIImage>()
    private let diskDirectory: URL?

    init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        if let caches {
            let dir = caches.appendingPathComponent("photo-frame-thumbnails", isDirectory: true)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            diskDirectory = dir
        } else {
            diskDirectory = nil
        }
    }

    func image(for key: String) -> UIImage? {
        if let cached = memoryCache.object(forKey: key as NSString) {
            return cached
        }
        guard let diskURL = fileURL(for: key), let data = try? Data(contentsOf: diskURL), let image = UIImage(data: data) else {
            return nil
        }
        memoryCache.setObject(image, forKey: key as NSString)
        return image
    }

    func set(_ image: UIImage, for key: String) {
        memoryCache.setObject(image, forKey: key as NSString)
        guard let diskURL = fileURL(for: key), let data = image.jpegData(compressionQuality: 0.7) else {
            return
        }
        try? data.write(to: diskURL, options: [.atomic])
    }

    private func fileURL(for key: String) -> URL? {
        guard let diskDirectory else { return nil }
        let safeName = key.data(using: .utf8)?.base64EncodedString()
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "=", with: "")
        return safeName.map { diskDirectory.appendingPathComponent($0).appendingPathExtension("jpg") }
    }
}

private actor ThumbnailDownloader {
    private let semaphore = AsyncSemaphore(2)
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.default
        config.httpMaximumConnectionsPerHost = 2
        session = URLSession(configuration: config)
    }

    func fetchData(from url: URL) async throws -> Data {
        await semaphore.wait()
        defer { Task { await semaphore.signal() } }
        let (data, _) = try await session.data(from: url)
        return data
    }
}

private actor AsyncSemaphore {
    private var count: Int
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(_ count: Int) {
        self.count = count
    }

    func wait() async {
        if count > 0 {
            count -= 1
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func signal() {
        if let waiter = waiters.first {
            waiters.removeFirst()
            waiter.resume()
        } else {
            count += 1
        }
    }
}

private let thumbnailDownloader = ThumbnailDownloader()

private struct PhotoThumbnailCell: View {
    let photo: PhotoFramePhoto
    let api: PhotoFrameAPI?
    @ObservedObject var cache: ThumbnailCache
    let isSelected: Bool
    let selectionAction: () -> Void

    @State private var thumbnail: UIImage?
    @State private var isLoading = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Rectangle()
                .fill(Color.secondary.opacity(0.15))
                .aspectRatio(1, contentMode: .fit)
                .overlay {
                    if let thumbnail {
                        Image(uiImage: thumbnail)
                            .resizable()
                            .scaledToFill()
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .clipped()
                    } else if isLoading {
                        ProgressView()
                    } else {
                        Image(systemName: "photo")
                            .foregroundStyle(.secondary)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 2))
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(isSelected ? Color.accentColor : Color.clear, lineWidth: 3)
                )

            if isSelected {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.white)
                    .padding(6)
            }
        }
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
        .onTapGesture(perform: selectionAction)
        .task(id: "\(photo.id)-\(photo.version)") { await loadThumbnail() }
    }

    private func loadThumbnail() async {
        if thumbnail != nil || isLoading {
            return
        }
        let cacheKey = "\(photo.filename)|\(photo.version)"
        if let cached = cache.image(for: cacheKey) {
            thumbnail = cached
            return
        }

        guard let url = photo.thumbnailURL ?? api?.photoURL(filename: photo.filename, version: photo.version, thumbnail: true) else {
            return
        }

        isLoading = true
        defer { isLoading = false }

        for _ in 0..<2 {
            do {
                let data = try await thumbnailDownloader.fetchData(from: url)
                if let image = UIImage(data: data) {
                    cache.set(image, for: cacheKey)
                    await MainActor.run {
                        thumbnail = image
                    }
                    return
                }
            } catch {
                continue
            }
        }
    }
}

// MARK: - Upload item model

@Observable
final class UploadItem: Identifiable {
    let id = UUID()
    let name: String
    var state: UploadState = .pending

    init(name: String) { self.name = name }
}

enum UploadState {
    case pending
    case uploading
    case done
    case failed(String)
}

// MARK: - Upload row

private struct UploadRowView: View {
    let item: UploadItem

    var body: some View {
        HStack {
            Text(item.name)
                .lineLimit(1)
            Spacer()
            switch item.state {
            case .pending:
                Image(systemName: "clock")
                    .foregroundStyle(.secondary)
            case .uploading:
                ProgressView()
                    .scaleEffect(0.8)
            case .done:
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            case .failed(let msg):
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundStyle(.red)
                    .help(msg)
            }
        }
    }
}

// MARK: - Upload sizing

private enum UploadSizePreset: String, CaseIterable, Identifiable {
    case hd720
    case fullHD
    case qhd1440
    case original

    var id: String { rawValue }

    var shortTitle: String {
        switch self {
        case .hd720: return "720p"
        case .fullHD: return "1080p"
        case .qhd1440: return "1440p"
        case .original: return "Original"
        }
    }

    var menuTitle: String {
        switch self {
        case .hd720: return "720p (1280 × 720)"
        case .fullHD: return "1080p (1920 × 1080)"
        case .qhd1440: return "1440p (2560 × 1440)"
        case .original: return "Original size"
        }
    }

    var maximumPixelSize: CGFloat? {
        switch self {
        case .hd720: return 1280
        case .fullHD: return 1920
        case .qhd1440: return 2560
        case .original: return nil
        }
    }

    var descriptionText: String {
        switch self {
        case .hd720: return "720p"
        case .fullHD: return "1080p"
        case .qhd1440: return "1440p"
        case .original: return "their original size"
        }
    }

    var compressionQuality: CGFloat { 0.85 }
}

private enum FrameUploadPreferenceStore {
    static func load(for frame: PhotoFrame) -> UploadSizePreset {
        let rawValue = UserDefaults.standard.string(forKey: key(for: frame))
        return rawValue.flatMap(UploadSizePreset.init(rawValue:)) ?? .fullHD
    }

    static func save(_ preset: UploadSizePreset, for frame: PhotoFrame) {
        UserDefaults.standard.set(preset.rawValue, forKey: key(for: frame))
    }

    private static func key(for frame: PhotoFrame) -> String {
        let identity = frame.host ?? frame.hostname ?? frame.id
        return "photoFrame.uploadPreset.\(identity)"
    }
}

// MARK: - Photo picker wrapper

struct PhotoSelection: @unchecked Sendable {
    let provider: NSItemProvider
    let suggestedFilename: String

    func loadAsJPEG(maxPixelSize: CGFloat?, compressionQuality: CGFloat = 0.85) async throws -> (Data, String) {
        let filename = URL(fileURLWithPath: suggestedFilename)
            .deletingPathExtension().lastPathComponent + ".jpg"
        let data = try await loadJPEGData(
            from: provider,
            maxPixelSize: maxPixelSize,
            compressionQuality: compressionQuality
        )
        return (data, filename)
    }

    private nonisolated func loadJPEGData(
        from provider: NSItemProvider,
        maxPixelSize: CGFloat?,
        compressionQuality: CGFloat
    ) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            _ = provider.loadObject(ofClass: UIImage.self) { object, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let image = object as? UIImage else {
                    continuation.resume(throwing: UploadError.conversionFailed)
                    return
                }

                let prepared = image.resizedForUpload(maxPixelSize: maxPixelSize)
                guard let data = prepared.jpegData(compressionQuality: compressionQuality) else {
                    continuation.resume(throwing: UploadError.conversionFailed)
                    return
                }
                continuation.resume(returning: data)
            }
        }
    }
}

private extension UIImage {
    func resizedForUpload(maxPixelSize: CGFloat?) -> UIImage {
        guard let maxPixelSize, maxPixelSize > 0 else {
            return self
        }

        let sourceSize = size
        let largestSide = max(sourceSize.width, sourceSize.height)
        guard largestSide > maxPixelSize else {
            return self
        }

        let scale = maxPixelSize / largestSide
        let targetSize = CGSize(
            width: max(1, (sourceSize.width * scale).rounded(.toNearestOrEven)),
            height: max(1, (sourceSize.height * scale).rounded(.toNearestOrEven))
        )

        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: targetSize, format: format).image { _ in
            draw(in: CGRect(origin: .zero, size: targetSize))
        }
    }
}

enum UploadError: LocalizedError {
    case conversionFailed
    var errorDescription: String? { "Could not convert image to JPEG." }
}

struct PhotoPicker: UIViewControllerRepresentable {
    let onComplete: ([PhotoSelection]) -> Void

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var config = PHPickerConfiguration()
        config.filter = .images
        config.selectionLimit = 0
        let picker = PHPickerViewController(configuration: config)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onComplete: onComplete) }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let onComplete: ([PhotoSelection]) -> Void
        init(onComplete: @escaping ([PhotoSelection]) -> Void) { self.onComplete = onComplete }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            let selections = results.map { result in
                let name = result.itemProvider.suggestedName
                    .map { "\($0).jpg" } ?? "photo_\(UUID().uuidString.prefix(8)).jpg"
                return PhotoSelection(provider: result.itemProvider, suggestedFilename: name)
            }
            onComplete(selections)
        }
    }
}
