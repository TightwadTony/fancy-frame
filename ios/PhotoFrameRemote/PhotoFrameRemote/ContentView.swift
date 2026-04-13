import SwiftUI

// MARK: - Status-bar height environment key

private struct StatusBarHeightKey: EnvironmentKey {
    static let defaultValue: CGFloat = 0
}

extension EnvironmentValues {
    var statusBarHeight: CGFloat {
        get { self[StatusBarHeightKey.self] }
        set { self[StatusBarHeightKey.self] = newValue }
    }
}

// MARK: - Root view

struct ContentView: View {
    var body: some View {
        GeometryReader { geo in
            DeviceListView()
                .environment(\.statusBarHeight, geo.safeAreaInsets.top)
        }
    }
}

private struct FancyFrameScreenBackgroundModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
    }
}

private struct FancyFrameScrollContentLayoutModifier: ViewModifier {
    let top: CGFloat
    let bottom: CGFloat

    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 12)
            .padding(.top, top)
            .padding(.bottom, bottom)
    }
}

extension View {
    func fancyFrameScreenBackground() -> some View {
        modifier(FancyFrameScreenBackgroundModifier())
    }

    func fancyFrameScrollContentLayout(top: CGFloat = 8, bottom: CGFloat = 12) -> some View {
        modifier(FancyFrameScrollContentLayoutModifier(top: top, bottom: bottom))
    }

    func fancyFrameTightTopScrollMargins() -> some View {
        contentMargins(.top, 0, for: .scrollContent)
            .contentMargins(.top, 0, for: .scrollIndicators)
    }
}
