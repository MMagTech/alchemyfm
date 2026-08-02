import SwiftUI

/// A station's page. Works for any station, not just the tuned one — opening it
/// starts nothing; only the play button does.
struct StationDetailView: View {
    let station: StationSummary

    @Environment(RadioPlayer.self) private var player
    @Environment(ServerConfig.self) private var server
    @State private var loader = StationDetailLoader()
    @State private var cast = CastController.shared

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

    /// Local heart state while a toggle is in flight, cleared when the track
    /// changes so a stale override can't bleed onto the next song.
    @State private var heartOverride: Bool?
    @State private var heartBusy = false

    private var facts: [KnowledgeFact] {
        showTrackTrivia ? (nowPlaying?.knowledge?.facts ?? []) : []
    }

    private var availableBio: String? {
        guard showArtistBio, let bio = nowPlaying?.artistBio, !bio.isEmpty else { return nil }
        return bio
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
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

                nowPlayingCard
                    .padding(.top, 18)

                if let upNext = detail?.upNext, !upNext.isEmpty {
                    section("Up next") { trackList(upNext) }
                        .padding(.top, 26)
                }

                if let recent = detail?.recentlyPlayed, !recent.isEmpty {
                    section("Recently played") { trackList(recent, showTime: true) }
                        .padding(.top, 20)
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
        .onChange(of: nowPlaying?.trackKey) { _, _ in heartOverride = nil }
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
                .font(.subheadline)
                .foregroundStyle(.tint)
                .multilineTextAlignment(.leading)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("About \(artist)")
        } else {
            Text(artist)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    /// The now-playing block as one bounded object.
    ///
    /// Whitespace and a hairline weren't enough separation to read as a
    /// grouping — an actual edge is what says "these belong together, the
    /// queue below does not".
    private var nowPlayingCard: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 8) {
                titleBlock
                Spacer(minLength: 0)
                // Output controls group together on the left; the heart is an
                // action on the track, not on where it plays, so it sits apart
                // at the trailing edge.
                //
                // Only worth the space once a receiver is actually on the
                // network — otherwise it's a button that opens an empty list.
                if cast.hasDevices {
                    CastButton(tint: cast.isCasting ? .accentColor : .secondary)
                        .frame(width: 34, height: 34)
                        .accessibilityLabel("Cast to a device")
                }

                // Bluetooth speakers and AirPlay destinations both live here.
                RoutePickerButton(tint: .secondary, activeTint: .accentColor)
                    .frame(width: 40, height: 40)
                    .accessibilityLabel("Choose audio output")

                if canHeart { heartButton }
            }

            listenButton
                .padding(.top, 16)

            statusLine
                .padding(.top, 10)
        }
        .frame(maxWidth: .infinity)
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(.secondarySystemBackground))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private var titleBlock: some View {
        VStack(alignment: .leading, spacing: 3) {
            // Wraps rather than scrolling: unlike the mini player this screen
            // has the room, and nothing else on it moves. Capped at two lines
            // so a 60-character title can't grow the card without limit.
            Text(nowPlaying?.title ?? station.name)
                .font(.title3.bold())
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            artistLine
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// While casting, where the audio is going matters more than the local
    /// player's state — which reads idle, because there is no local player.
    private var castingLabel: String? {
        guard isTuned, cast.isCasting else { return nil }
        return cast.deviceName.map { "Casting to \($0)" } ?? "Casting"
    }

    /// Sits below the transport, annotating it. Between the artist and the
    /// play button it just split the metadata away from its own heading.
    private var statusLine: some View {
        HStack(spacing: 6) {
            if isTuned && player.isPlaying {
                Circle().fill(.green).frame(width: 6, height: 6)
            }
            // Only the tuned station has playback state worth reporting; for
            // anything else this page is just a preview.
            Text(castingLabel ?? (isTuned ? player.statusText : (onAir ? "On air" : "Off air")))
            if onAir {
                Text("·")
                Text(listeners == 1 ? "1 listener" : "\(listeners) listeners")
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    /// The heart is an operator action, so it only exists once the server has
    /// told us the current heart state — which it only does for a signed-in
    /// admin. No sign-in, no `hearted`, no button.
    private var isHearted: Bool {
        heartOverride ?? (nowPlaying?.hearted ?? false)
    }

    private var canHeart: Bool {
        nowPlaying?.hearted != nil && nowPlaying?.itemId != nil
    }

    private var heartButton: some View {
        Button(action: toggleHeart) {
            Image(systemName: isHearted ? "heart.fill" : "heart")
                .font(.title3)
                .foregroundStyle(isHearted ? .pink : .secondary)
                .frame(width: 40, height: 40)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(heartBusy)
        .accessibilityLabel(isHearted ? "Remove heart" : "Heart this track")
    }

    /// Labelled rather than an icon, because "play" is the wrong verb here.
    /// On live radio it means *tune in to what is already happening*, not
    /// resume — and no glyph conveys that. No skip, no seek: everyone hears
    /// the same broadcast.
    private var listenButton: some View {
        let isLive = isTuned && player.wantsLive

        return Button {
            if isTuned {
                player.toggle()
            } else if let api = server.api {
                player.tune(to: detail?.summary ?? station, api: api)
            }
        } label: {
            HStack(spacing: 8) {
                if isTuned && player.isBusy {
                    ProgressView()
                        .tint(.white)
                } else {
                    Image(systemName: isLive ? "stop.fill" : "play.fill")
                }
                Text(isLive ? "Stop" : "Listen live")
                    .font(.headline)
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(.tint)
            )
        }
        .buttonStyle(.plain)
    }

    private func toggleHeart() {
        guard let api = server.api, let itemId = nowPlaying?.itemId else { return }
        let target = !isHearted
        // Optimistic: the poll that would confirm it is up to 5s away when
        // browsing, and a heart that lags that far feels broken.
        heartOverride = target
        heartBusy = true
        Task {
            do {
                heartOverride = try await api.setHeart(itemId: itemId, hearted: target)
            } catch {
                // Fall back to whatever the server says next poll.
                heartOverride = nil
            }
            heartBusy = false
        }
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
