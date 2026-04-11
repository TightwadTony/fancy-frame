import SwiftUI
import PhotosUI
import UIKit

struct PhotosView: View {
    let frame: PhotoFrame

    @State private var photoCount: Int?
    @State private var isLoadingCount = true
    @State private var showPicker = false
    @State private var uploads: [UploadItem] = []
    @State private var isUploading = false

    var body: some View {
        List {
            Section {
                HStack {
                    if isLoadingCount {
                        ProgressView()
                            .scaleEffect(0.7)
                    } else {
                        Image(systemName: "photo.stack")
                            .foregroundStyle(.secondary)
                        Text(photoCount.map { "\($0) photo\($0 == 1 ? "" : "s") on frame" } ?? "Unknown")
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section {
                Button {
                    showPicker = true
                } label: {
                    Label("Add Photos", systemImage: "plus.circle")
                }
                .disabled(isUploading)
            }

            if !uploads.isEmpty {
                Section("Uploads") {
                    ForEach(uploads) { item in
                        UploadRowView(item: item)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Photos")
        .navigationBarTitleDisplayMode(.large)
        .sheet(isPresented: $showPicker) {
            PhotoPicker { selections in
                showPicker = false
                guard !selections.isEmpty else { return }
                startUploads(selections)
            }
        }
        .task { await loadCount() }
    }

    // MARK: - Network

    private func loadCount() async {
        guard let api = frame.api else {
            isLoadingCount = false
            return
        }
        isLoadingCount = true
        if let count = try? await api.fetchPhotoCount() {
            photoCount = count
        }
        isLoadingCount = false
    }

    private func startUploads(_ selections: [PhotoSelection]) {
        let newItems = selections.map { UploadItem(name: $0.suggestedFilename) }
        uploads.append(contentsOf: newItems)
        isUploading = true

        Task {
            for (index, selection) in selections.enumerated() {
                let item = newItems[index]
                await uploadOne(selection: selection, item: item)
            }
            isUploading = false
            // Refresh count after all uploads finish
            await loadCount()
        }
    }

    private func uploadOne(selection: PhotoSelection, item: UploadItem) async {
        guard let api = frame.api else {
            item.state = .failed("Frame not reachable")
            return
        }
        item.state = .uploading

        do {
            let (jpegData, filename) = try await selection.loadAsJPEG()
            try await api.uploadPhoto(jpegData, filename: filename)
            item.state = .done
        } catch {
            item.state = .failed(error.localizedDescription)
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

// MARK: - Photo picker wrapper

struct PhotoSelection: @unchecked Sendable {
    let provider: NSItemProvider
    let suggestedFilename: String

    func loadAsJPEG() async throws -> (Data, String) {
        let filename = URL(fileURLWithPath: suggestedFilename)
            .deletingPathExtension().lastPathComponent + ".jpg"
        let data = try await loadJPEGData(from: provider)
        return (data, filename)
    }

    private nonisolated func loadJPEGData(from provider: NSItemProvider) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            _ = provider.loadObject(ofClass: UIImage.self) { object, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let image = object as? UIImage,
                      let data = image.jpegData(compressionQuality: 0.85) else {
                    continuation.resume(throwing: UploadError.conversionFailed)
                    return
                }
                continuation.resume(returning: data)
            }
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
