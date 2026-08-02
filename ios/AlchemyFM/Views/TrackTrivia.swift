import SwiftUI

/// The ⓘ affordance the web player puts on the cover art. Only rendered when
/// the current track actually has facts, so its presence is itself the signal
/// that there's something to read.
struct TriviaBadge: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "info")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.tint)
                .frame(width: 28, height: 28)
                .background(.ultraThinMaterial, in: Circle())
                .overlay(
                    Circle().strokeBorder(Color.accentColor.opacity(0.55), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Track trivia")
    }
}

/// The facts for the current track, each with its category and source.
///
/// The web player rotates these on a 15s timer in a small popover. A sheet has
/// room to show them all, and a reader holding the phone shouldn't have text
/// advancing mid-sentence — so they're stacked and separated rather than paged.
struct TrackTriviaSheet: View {
    let facts: [KnowledgeFact]
    let trackTitle: String

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    ForEach(Array(facts.enumerated()), id: \.offset) { index, fact in
                        if index > 0 { Divider() }
                        factRow(fact)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
            }
            .navigationTitle(trackTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func factRow(_ fact: KnowledgeFact) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(Self.categoryLabel(fact.category))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.tint)
                .textCase(.uppercase)

            Text(fact.text)
                .font(.callout)

            // Generated from web search, so the citation is the only way a
            // reader can check a specific claim.
            ForEach(Array(fact.sources.enumerated()), id: \.offset) { _, source in
                if let url = URL(string: source.url) {
                    Link(destination: url) {
                        Label(
                            source.title.isEmpty ? (url.host() ?? "Source") : source.title,
                            systemImage: "arrow.up.right"
                        )
                        .font(.caption)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private static func categoryLabel(_ raw: String) -> String {
        switch raw {
        case "song_fact": return "Song"
        case "album_fact": return "Album"
        case "artist_fact": return "Artist"
        default: return raw.replacingOccurrences(of: "_", with: " ")
        }
    }
}
