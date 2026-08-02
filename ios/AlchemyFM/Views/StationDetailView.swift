import SwiftUI

/// A station's page. Works for any station, not just the tuned one — opening it
/// starts nothing; only the play button does.
struct StationDetailView: View {
    let station: StationSummary

    @Environment(RadioPlayer.self) private var player
    @Environment(ServerConfig.self) private var server
    @State private var loader = StationDetailLoader()

    @AppStorage(DisplayPreference.showArtistBio) private var showArtistBio = true
    @AppStorage(DisplayPreference.showTrackTrivia) private var showTrackTrivia = true

    private var isTuned: Bool { player.isTuned(to: station.slug) }

    /// When this *is* the tuned station, reuse the player's fast poll rather
    /// than running a second loop against the same endpoint.
    private var detail: StationDetail? {
        isTuned ? player.detail : loader.detail
    }

    private var nowPlaying: NowPlaying? {
        detail?.nowPlaying ?? station.nowPlaying
    }

    private var listeners: Int { detail?.listeners ?? station.listeners }
    private var onAir: Bool { detail?.onAir ?? station.onAir }

    /// One case per slide-up, carrying its content.
    ///
    /// The content travels with the presentation rather than living in separate
    /// @State read by `.sheet(isPresented:)` — that renders an empty sheet,
    /// because the body builds before the state lands. A single enum also keeps
    /// this to one `.sheet` modifier; stacking two on one view is unreliable.
    private enum DetailSheet: Identifiable {
        case trivia(key: String, facts: [KnowledgeFact], trackTitle: String)
        case bio(key: String, artist: String, text: String, source: URL?)

        var id: String {
            switch self {
            case .trivia(let key, _, _): return "trivia-\(key)"
            case .bio(let key, _, _, _): return "bio-\(key)"
            }
        }
    }

    @State private var sheet: DetailSheet?

    private var facts: [KnowledgeFact] {
        showTrackTrivia ? (nowPlaying?.knowledge?.facts ?? []) : []
    }

    private var availableBio: String? {
        guard showArtistBio, let bio = nowPlaying?.artistBio, !bio.isEmpty else { return nil }
        return bio
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Artwork(path: nowPlaying?.coverUrl ?? station.listArtwork,
                        api: server.api,
                        cornerRadius: 16)
                    .frame(maxWidth: 320)
                    .aspectRatio(1, contentMode: .fit)
                    .shadow(radius: 18, y: 8)
                    .overlay(alignment: .bottomTrailing) {
                        if !facts.isEmpty {
                            TriviaBadge {
                                // Snapshot on open: the station keeps polling,
                                // and a track change shouldn't swap the text
                                // out from under someone mid-read.
                                sheet = .trivia(
                                    key: nowPlaying?.trackKey ?? station.slug,
                                    facts: facts,
                                    trackTitle: nowPlaying?.title ?? station.name
                                )
                            }
                            .padding(10)
                        }
                    }
                    .padding(.top, 8)

                trackInfo
                transport

                if let upNext = detail?.upNext, !upNext.isEmpty {
                    section("Up next") { trackList(upNext) }
                }

                if let recent = detail?.recentlyPlayed, !recent.isEmpty {
                    section("Recently played") { trackList(recent, showTime: true) }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 32)
        }
        .navigationTitle(station.name)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $sheet) { item in
            switch item {
            case .trivia(_, let facts, let trackTitle):
                TrackTriviaSheet(facts: facts, trackTitle: trackTitle)
            case .bio(_, let artist, let text, let source):
                ArtistBioSheet(artist: artist, text: text, sourceURL: source)
            }
        }
        .task(id: isTuned) {
            guard let api = server.api else { return }
            if isTuned {
                loader.stop()
            } else {
                loader.start(slug: station.slug, api: api)
            }
        }
        .onDisappear { loader.stop() }
    }

    /// Tapping the artist's name opens their biography — a self-labelling
    /// target, which is why this is here rather than a second unlabelled glyph
    /// on the artwork competing with the trivia badge.
    @ViewBuilder
    private var artistLine: some View {
        let artist = nowPlaying?.artist ?? station.description

        if let bio = availableBio, !artist.isEmpty {
            Button {
                sheet = .bio(
                    key: artist,
                    artist: artist,
                    text: bio,
                    source: nowPlaying?.artistBioUrl.flatMap(URL.init(string:))
                )
            } label: {
                HStack(spacing: 5) {
                    Text(artist)
                    Image(systemName: "info.circle")
                        .font(.footnote)
                }
                .font(.title3)
                .foregroundStyle(.tint)
                .multilineTextAlignment(.center)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("About \(artist)")
        } else {
            Text(artist)
                .font(.title3)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    private var trackInfo: some View {
        VStack(spacing: 6) {
            Text(nowPlaying?.title ?? station.name)
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            artistLine

            HStack(spacing: 6) {
                if isTuned && player.isPlaying {
                    Circle().fill(.green).frame(width: 6, height: 6)
                }
                // Only the tuned station has playback state worth reporting;
                // for anything else this page is just a preview.
                Text(isTuned ? player.statusText : (onAir ? "On air" : "Off air"))
                if onAir {
                    Text("·")
                    Text(listeners == 1 ? "1 listener" : "\(listeners) listeners")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.top, 2)
        }
    }

    private var transport: some View {
        // No skip, no seek — everyone hears the same broadcast.
        StationPlayButton(station: detail?.summary ?? station, size: .largeTitle)
            .frame(width: 72, height: 72)
            .background(.quaternary, in: Circle())
    }

    private func trackList(_ tracks: [TrackRef], showTime: Bool = false) -> some View {
        VStack(spacing: 10) {
            ForEach(Array(tracks.enumerated()), id: \.offset) { _, track in
                HStack(spacing: 10) {
                    Artwork(path: track.coverUrl, api: server.api, cornerRadius: 4)
                        .frame(width: 36, height: 36)

                    VStack(alignment: .leading, spacing: 1) {
                        Text(track.title)
                            .font(.subheadline)
                            .lineLimit(1)
                        Text(track.artist)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 0)

                    if showTime, let playedAt = track.playedAt, playedAt != .distantPast {
                        Text(playedAt, format: .relative(presentation: .numeric))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
        }
    }

    private func section<Content: View>(
        _ title: String, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 4)
    }
}
