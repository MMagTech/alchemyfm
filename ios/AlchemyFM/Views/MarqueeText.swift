import SwiftUI

/// Single-line text that slides to reveal its tail when it doesn't fit.
///
/// Used only in the mini player, where the bar is permanently on screen and a
/// truncated title has nowhere else to be read. Everywhere else has room to
/// wrap or a detail screen to open, and text that moves on its own is a cost
/// rather than a feature — so this deliberately isn't applied globally.
struct MarqueeText: View {
    let text: String
    var font: Font = .subheadline
    var weight: Font.Weight?
    /// Points per second. An unhurried read, not an LED ticker — this sits in
    /// a bar that's permanently on screen, so speed is the wrong thing to
    /// optimise for.
    var speed: Double = 15
    /// Pause at each end, so the start and the end are each legible at rest.
    var pause: Double = 2.5

    @State private var textWidth: CGFloat = 0
    @State private var boxWidth: CGFloat = 0
    @State private var shifted = false

    private var overflow: CGFloat { max(0, textWidth - boxWidth) }

    private var resolvedFont: Font {
        weight.map { font.weight($0) } ?? font
    }

    var body: some View {
        // A hidden, truncating copy does the layout — it compresses under
        // pressure exactly like a plain Text would. The visible copy is
        // fixedSize (so it can be wider than the box) and lives in an overlay,
        // where its intrinsic width can't push its siblings around.
        Text(text)
            .font(resolvedFont)
            .lineLimit(1)
            .hidden()
            .overlay(alignment: .leading) {
                Text(text)
                    .font(resolvedFont)
                    .lineLimit(1)
                    .fixedSize()
                    .background(
                        GeometryReader { proxy in
                            Color.clear
                                .preference(key: MarqueeWidthKey.self, value: proxy.size.width)
                        }
                    )
                    .offset(x: shifted ? -overflow : 0)
            }
            .onPreferenceChange(MarqueeWidthKey.self) { textWidth = $0 }
            .background(
                GeometryReader { proxy in
                    Color.clear.preference(key: MarqueeBoxKey.self, value: proxy.size.width)
                }
            )
            .onPreferenceChange(MarqueeBoxKey.self) { boxWidth = $0 }
            .clipped()
            // Softens the clip into a fade, but only when the text actually
            // overflows — masking a short title would dim its own first and
            // last letters for no reason.
            .mask(overflow > 0 ? AnyView(edgeFade) : AnyView(Rectangle()))
            .onChange(of: text) { _, _ in restart() }
            .onChange(of: overflow) { _, _ in restart() }
            .onAppear { restart() }
    }

    private var edgeFade: some View {
        LinearGradient(
            stops: [
                .init(color: .clear, location: 0),
                .init(color: .black, location: 0.04),
                .init(color: .black, location: 0.94),
                .init(color: .clear, location: 1),
            ],
            startPoint: .leading,
            endPoint: .trailing
        )
    }

    private func restart() {
        // Drop any in-flight repeatForever animation before starting another,
        // otherwise they stack and the text drifts.
        withAnimation(.none) { shifted = false }
        guard overflow > 0 else { return }
        withAnimation(
            .linear(duration: Double(overflow) / speed)
                .delay(pause)
                .repeatForever(autoreverses: true)
        ) {
            shifted = true
        }
    }
}

private struct MarqueeWidthKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct MarqueeBoxKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}
