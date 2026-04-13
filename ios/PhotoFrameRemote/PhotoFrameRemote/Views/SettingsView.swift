import SwiftUI

struct SettingsView: View {
    let frame: PhotoFrame
    @Environment(\.statusBarHeight) private var statusBarHeight
    @Environment(\.dismiss) private var dismiss

    @State private var config: PhotoFrameConfig = .default
    @State private var isLoading   = true
    @State private var isSaving    = false
    @State private var error: String?
    @State private var savedConfig: PhotoFrameConfig = .default
    @State private var showRestartConfirm = false
    @State private var showRestartSuccess = false

    private var hasChanges: Bool { config != savedConfig }

    var body: some View {
        Form {
            Section {
                HStack {
                    Button { dismiss() } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "chevron.left").fontWeight(.semibold)
                            Text("Back")
                        }
                        .font(.body)
                    }
                    Spacer()
                    Text("Settings").font(.headline)
                    Spacer()
                    Color.clear.frame(width: 60, height: 1)
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 4, leading: 12, bottom: 4, trailing: 12))
            }
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
                frameSection
                slideshowSection
                kenBurnsSection
                shareSection
                restartSection
            }
        }
        .contentMargins(.top, statusBarHeight, for: .scrollContent)
        .ignoresSafeArea(edges: .top)
        .toolbar(.hidden, for: .navigationBar)
        .scrollContentBackground(.hidden)
        .fancyFrameScreenBackground()
        .toolbar {
            ToolbarItem(placement: .bottomBar) {
                if hasChanges {
                    Button(action: save) {
                        if isSaving {
                            ProgressView()
                        } else {
                            Text("Apply Changes")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(isSaving)
                }
            }
        }
        .alert("Error", isPresented: .constant(error != nil), actions: {
            Button("OK") { error = nil }
        }, message: {
            Text(error ?? "")
        })
        .alert("Restart Photo Frame?", isPresented: $showRestartConfirm) {
            Button("Restart", role: .destructive) { sendRestart() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The frame will restart and be unavailable for about 30 seconds.")
        }
        .alert("Restarting…", isPresented: $showRestartSuccess) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("The photo frame is rebooting.")
        }
        .task { await load() }
    }

    // MARK: - Sections

    private var frameSection: some View {
        Section("Frame") {
            TextField("Display Name", text: $config.frameName)
                .textInputAutocapitalization(.words)
                .autocorrectionDisabled(true)
        }
    }

    private var slideshowSection: some View {
        Section("Slideshow") {
            Stepper(value: $config.slideSeconds, in: 1...300, step: 1) {
                LabeledContent("Slide Duration") {
                    Text("\(Int(config.slideSeconds)) s")
                        .foregroundStyle(.secondary)
                }
            }

            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Transition Duration") {
                    Text(String(format: "%.1f s", config.fadeSeconds))
                        .foregroundStyle(.secondary)
                }
                Slider(value: $config.fadeSeconds, in: 0...5, step: 0.1)
                    .tint(.accentColor)
            }
            .padding(.vertical, 2)

            NavigationLink {
                TransitionsPickerView(selected: $config.transitions)
            } label: {
                LabeledContent("Transitions") {
                    Text(transitionsSummary)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var kenBurnsSection: some View {
        Section("Ken Burns Effect") {
            Toggle("Enable Ken Burns", isOn: $config.kenBurns)

            if config.kenBurns {
                VStack(alignment: .leading, spacing: 6) {
                    LabeledContent("Zoom Range") {
                        Text(String(format: "%.2f – %.2f", config.kenBurnsZoomMin, config.kenBurnsZoomMax))
                            .foregroundStyle(.secondary)
                    }
                    HStack(spacing: 8) {
                        Text("1.0×").font(.caption).foregroundStyle(.secondary)
                        Slider(
                            value: $config.kenBurnsZoomMin,
                            in: 1.0...config.kenBurnsZoomMax,
                            step: 0.01
                        )
                        .tint(.accentColor)
                        Text("min").font(.caption).foregroundStyle(.secondary)
                    }
                    HStack(spacing: 8) {
                        Text("1.0×").font(.caption).foregroundStyle(.secondary)
                        Slider(
                            value: $config.kenBurnsZoomMax,
                            in: config.kenBurnsZoomMin...2.0,
                            step: 0.01
                        )
                        .tint(.accentColor)
                        Text("max").font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 2)
            }
        }
    }

    private var shareSection: some View {
        Section("Share") {
            NavigationLink {
                SambaSettingsView(frame: frame)
            } label: {
                Label("Share Settings", systemImage: "folder.badge.gearshape")
            }
        }
    }

    private var restartSection: some View {
        Section {
            Button(role: .destructive) {
                showRestartConfirm = true
            } label: {
                HStack {
                    Spacer()
                    Text("Restart Photo Frame…")
                    Spacer()
                }
            }
        }
    }

    // MARK: - Helpers

    private var transitionsSummary: String {
        switch config.transitions.count {
        case 0: return "None"
        case 1: return config.transitions[0].replacingOccurrences(of: "_", with: " ").capitalized
        case PhotoFrameConfig.default.transitions.count: return "All"
        default: return "\(config.transitions.count) selected"
        }
    }

    // MARK: - Network

    private func load() async {
        guard let api = frame.api else {
            isLoading = false
            error = "Frame is not reachable."
            return
        }
        do {
            let c = try await api.fetchConfig()
            config      = c
            savedConfig = c
            frame.updateDisplayName(c.frameName)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    private func save() {
        guard let api = frame.api else { return }
        let trimmedName = config.frameName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else {
            error = "Display name cannot be empty."
            return
        }

        config.frameName = trimmedName
        isSaving = true
        Task {
            do {
                let updated = try await api.updateConfig(config)
                config      = updated
                savedConfig = updated
                frame.updateDisplayName(updated.frameName)
            } catch {
                self.error = error.localizedDescription
            }
            isSaving = false
        }
    }

    private func sendRestart() {
        guard let api = frame.api else { return }
        Task {
            do {
                try await api.restart()
                showRestartSuccess = true
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
