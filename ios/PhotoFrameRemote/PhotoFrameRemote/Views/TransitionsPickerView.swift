import SwiftUI

struct TransitionsPickerView: View {
    @Binding var selected: [String]
    @Environment(\.dismiss) private var dismiss

    private let allTransitions: [(id: String, label: String, description: String)] = [
        ("crossfade",    "Crossfade",     "Smooth alpha blend between slides"),
        ("fade_to_black","Fade to Black", "Fade out, then fade in next slide"),
        ("wipe",         "Wipe",          "Hard edge sweeps across the screen"),
    ]

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.headline.weight(.semibold))
                        .frame(width: 40, height: 40)
                        .background(Color(.secondarySystemGroupedBackground))
                        .clipShape(Circle())
                }
                Spacer()
                Text("Transitions")
                    .font(.title2.bold())
                Spacer()
                Color.clear
                    .frame(width: 40, height: 40)
            }
            .padding(.horizontal, 12)
            .padding(.top, 0)
            .padding(.bottom, 6)
            .background(Color(.systemGroupedBackground).ignoresSafeArea(edges: .top))

            Form {
                Section {
                    ForEach(allTransitions, id: \.id) { transition in
                        Button {
                            toggle(transition.id)
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(transition.label)
                                        .font(.body)
                                        .foregroundStyle(.primary)
                                    Text(transition.description)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if selected.contains(transition.id) {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundStyle(.tint)
                                        .font(.title3)
                                } else {
                                    Image(systemName: "circle")
                                        .foregroundStyle(.tertiary)
                                        .font(.title3)
                                }
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                } footer: {
                    Text("At least one transition must remain selected.")
                }
            }
        }
        .background(Color(.systemGroupedBackground).ignoresSafeArea())
        .toolbar(.hidden, for: .navigationBar)
    }

    private func toggle(_ id: String) {
        if selected.contains(id) {
            // Prevent deselecting the last one
            guard selected.count > 1 else { return }
            selected.removeAll { $0 == id }
        } else {
            selected.append(id)
        }
    }
}
