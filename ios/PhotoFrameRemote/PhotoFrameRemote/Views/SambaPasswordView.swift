import SwiftUI

struct SambaPasswordView: View {
    let frame: PhotoFrame
    @Environment(\.statusBarHeight) private var statusBarHeight
    @Environment(\.dismiss) private var dismiss

    @State private var currentPassword  = ""
    @State private var newPassword      = ""
    @State private var confirmPassword  = ""
    @State private var isChanging       = false
    @State private var error: String?
    @State private var showSuccess      = false

    private var passwordsMatch: Bool { newPassword == confirmPassword }
    private var canSubmit: Bool {
        !currentPassword.isEmpty &&
        !newPassword.isEmpty &&
        newPassword.count >= 6 &&
        passwordsMatch &&
        !isChanging
    }

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
                    Text("Change Password").font(.headline)
                    Spacer()
                    Color.clear.frame(width: 60, height: 1)
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 4, leading: 12, bottom: 4, trailing: 12))
            }

            Section("Current Password") {
                SecureField("Current Password", text: $currentPassword)
                    .textContentType(.password)
                    .autocorrectionDisabled(true)
                    .textInputAutocapitalization(.never)
            }

            Section {
                SecureField("New Password", text: $newPassword)
                    .textContentType(.newPassword)
                    .autocorrectionDisabled(true)
                    .textInputAutocapitalization(.never)
                SecureField("Confirm New Password", text: $confirmPassword)
                    .textContentType(.newPassword)
                    .autocorrectionDisabled(true)
                    .textInputAutocapitalization(.never)
            } header: {
                Text("New Password")
            } footer: {
                if !newPassword.isEmpty && !confirmPassword.isEmpty && !passwordsMatch {
                    Text("Passwords do not match.")
                        .foregroundStyle(.red)
                } else if !newPassword.isEmpty && newPassword.count < 6 {
                    Text("Password must be at least 6 characters.")
                        .foregroundStyle(.red)
                }
            }

            Section {
                Button(action: changePassword) {
                    HStack {
                        Spacer()
                        if isChanging {
                            ProgressView()
                        } else {
                            Text("Change Password")
                                .fontWeight(.semibold)
                        }
                        Spacer()
                    }
                }
                .disabled(!canSubmit)
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
        .alert("Password Changed", isPresented: $showSuccess) {
            Button("OK") { dismiss() }
        } message: {
            Text("The Samba share password has been updated successfully.")
        }
    }

    // MARK: - Network

    private func changePassword() {
        guard let api = frame.api else { return }
        isChanging = true
        Task {
            do {
                try await api.changeSambaPassword(current: currentPassword, new: newPassword)
                showSuccess = true
            } catch {
                self.error = error.localizedDescription
            }
            isChanging = false
        }
    }
}
