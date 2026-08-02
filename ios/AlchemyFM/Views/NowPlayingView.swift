import SwiftUI

struct NowPlayingView: View {
    @Environment(RadioPlayer.self) private var player
    @Environment(ServerConfig.self) private var server

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Artwork(path: player.nowPlaying?.coverUrl ?? player.station?.artworkUrl,
                        api: server.api,
                        cornerRadius: 16)
                    .frame(maxWidth: 320)
                    .aspectRatio(1, contentMode: .fit)
                    .shadow(radius: 18, y: 8)
                    .padding(.top, 8)

                trackInfo
                transport

                if let bio = player.nowPlaying?.artistBio, !bio.isEmpty {
                    section("About \(player.nowPlaying?.artist ?? "the artist")") {
                        Text(bio)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }

                if let facts = player.nowPlaying?.knowledge?.facts, !facts.isEmpty {
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

                if let upNext = player.detail?.upNext, !upNext.isEmpty {
                    section("Up next") { trackList(upNext) }
                }

                if let recent = player.detail?.recentlyPlayed, !recent.isEmpty {
                    section("Recently played") { trackList(recent, showTime: true) }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 32)
        }
        .navigationTitle(player.station?.name ?? "Now Playing")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var trackInfo: some View {
        VStack(spacing: 6) {
            Text(player.nowPlaying?.title ?? player.station?.name ?? "")
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            Text(player.nowPlaying?.artist ?? "")
                .font(.title3)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            HStack(spacing: 6) {
                if player.isPlaying {
                    Circle().fill(.green).frame(width: 6, height: 6)
                }
                Text(player.statusText)
                if let station = player.station, station.onAir {
                    Text("·")
                    Text(station.listeners == 1 ? "1 listener" : "\(station.listeners) listeners")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.top, 2)
        }
    }

    private var transport: some View {
        // No skip, no seek — everyone hears the same broadcast.
        PlayStopButton(size: .largeTitle)
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
