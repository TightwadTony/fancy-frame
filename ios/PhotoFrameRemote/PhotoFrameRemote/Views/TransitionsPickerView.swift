import SwiftUI

struct TransitionsPickerView: View {
    @Binding var selected: [String]

    private let allTransitions: [(id: String, label: String, description: String)] = [
        ("crossfade",    "Crossfade",     "Smooth alpha blend between slides"),
        ("fade_to_black","Fade to Black", "Fade out, then fade in next slide"),
        ("wipe",         "Wipe",          "Hard edge sweeps across the screen"),
    ]

    var body: some View {
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
        .navigationTitle("Transitions")
        .navigationBarTitleDisplayMode(.large)
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
