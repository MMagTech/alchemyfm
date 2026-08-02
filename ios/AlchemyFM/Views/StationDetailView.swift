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

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Artwork(path: nowPlaying?.coverUrl ?? station.listArtwork,
                        api: server.api,
                        cornerRadius: 16)
                    .frame(maxWidth: 320)
                    .aspectRatio(1, contentMode: .fit)
                    .shadow(radius: 18, y: 8)
                    .padding(.top, 8)

                trackInfo
                transport

                if showArtistBio, let bio = nowPlaying?.artistBio, !bio.isEmpty {
                    section("About \(nowPlaying?.artist ?? "the artist")") {
                        Text(bio)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }

                if showTrackTrivia, let facts = nowPlaying?.knowledge?.facts, !facts.isEmpty {
                    section("Did you know") {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(Array(facts.enumerated()), id: \.offset) { _, fact in
                                Text(fact.text)
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

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

    private var trackInfo: some View {
        VStack(spacing: 6) {
            Text(nowPlaying?.title ?? station.name)
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            Text(nowPlaying?.artist ?? station.description)
                .font(.title3)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

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
