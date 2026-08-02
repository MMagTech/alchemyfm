import SwiftUI

/// The artist biography, opened by tapping the artist's name.
///
/// Same slide-up treatment as the trivia sheet. Keeping it off the station page
/// matters more than it sounds: bios run to a few thousand characters and used
/// to push "Up next" below the fold on every single track.
struct ArtistBioSheet: View {
    let artist: String
    let text: String
    let sourceURL: URL?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(text)
                        .font(.callout)

                    if let sourceURL {
                        Link(destination: sourceURL) {
                            Label("Read more on Last.fm", systemImage: "arrow.up.right")
                                .font(.caption)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
            }
            .navigationTitle(artist)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
