import SwiftUI

struct SambaSettingsView: View {
    let frame: PhotoFrame
    @Environment(\.statusBarHeight) private var statusBarHeight
    @Environment(\.dismiss) private var dismiss

    @State private var settings: SambaSettings?
    @State private var isLoading        = true
    @State private var isSaving         = false
    @State private var error: String?
    @State private var showEnableGuestConfirm = false
    /// Optimistic pending value while a network save is in flight.
    @State private var pendingGuestAccess: Bool? = nil

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
                    Text("Share Settings").font(.headline)
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
                guestAccessSection
                if (pendingGuestAccess ?? settings?.guestAccess) == false {
                    passwordSection
                }
            }
        }
        .contentMargins(.top, statusBarHeight, for: .scrollContent)
        .ignoresSafeArea(edges: .top)
        .toolbar(.hidden, for: .navigationBar)
        .scrollContentBackground(.hidden)
        .fancyFrameScreenBackground()
        .alert("Error", isPresented: .constant(error != nil), actions: {
            Button("OK") { error = nil }
        }, message: {
            Text(error ?? "")
        })
        .alert("Allow Guest Access?", isPresented: $showEnableGuestConfirm) {
            Button("Enable Guest Access", role: .destructive) { setGuestAccess(true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Anyone on your network will be able to browse and upload photos without a password. This is not recommended.")
        }
        .task { await load() }
    }

    // MARK: - Sections

    private var guestAccessSection: some View {
        Section {
            Toggle(isOn: guestAccessBinding) {
                Label("Allow Guest Access", systemImage: "person.2.fill")
                    .foregroundStyle((pendingGuestAccess ?? settings?.guestAccess ?? false) ? .orange : .primary)
            }
            .tint(.orange)
            .disabled(isSaving)
        } footer: {
            Text("⚠ Guest access allows anyone on your local network to browse and upload photos without a password. This is not recommended for most home networks.")
                .foregroundStyle(.orange)
        }
    }

    private var passwordSection: some View {
        Section("Password") {
            NavigationLink {
                SambaPasswordView(frame: frame)
            } label: {
                Label("Change Share Password…", systemImage: "lock.rotation")
            }
        }
    }

    // MARK: - Helpers

    private var guestAccessBinding: Binding<Bool> {
        Binding(
            get: { pendingGuestAccess ?? settings?.guestAccess ?? false },
            set: { newValue in
                if newValue {
                    showEnableGuestConfirm = true
                } else {
                    setGuestAccess(false)
                }
            }
        )
    }

    // MARK: - Network

    private func load() async {
        guard let api = frame.api else {
            isLoading = false
            error = "Frame is not reachable."
            return
        }
        do {
            settings = try await api.fetchSambaSettings()
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    private func setGuestAccess(_ enabled: Bool) {
        guard let api = frame.api else { return }
        pendingGuestAccess = enabled  // optimistic update — toggle reflects new state immediately
        isSaving = true
        Task {
            do {
                settings = try await api.setSambaGuestAccess(enabled)
            } catch {
                pendingGuestAccess = nil  // revert on error
                self.error = error.localizedDescription
            }
            pendingGuestAccess = nil
            isSaving = false
        }
    }
}
