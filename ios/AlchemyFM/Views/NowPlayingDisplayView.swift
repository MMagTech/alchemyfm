import SwiftUI

/// Lean-back display mode: the phone propped on a desk showing what's playing.
///
/// Reads values passed in rather than fetching anything — the station page
/// behind it is still polling, and a `fullScreenCover`'s content re-evaluates
/// when its parent's state changes, so this stays live for free.
struct NowPlayingDisplayView: View {
    let station: StationSummary
    let nowPlaying: NowPlaying?
    let isTuned: Bool

    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(ServerConfig.self) private var server
    @Environment(RadioPlayer.self) private var player

    /// Set on appear from `isTuned`. Once following, the screen tracks the
    /// player rather than the station it was opened with — otherwise skipping
    /// would change what's playing while this kept showing the old station.
    @State private var following = false
    @State private var controlsVisible = true
    @State private var hideTask: Task<Void, Never>?
    @State private var dragOffset: CGFloat = 0

    private var shownStation: StationSummary {
        following ? (player.station ?? station) : station
    }

    private var shownNowPlaying: NowPlaying? {
        following ? player.nowPlaying : nowPlaying
    }

    private var artworkPath: String? {
        shownNowPlaying?.coverUrl ?? shownStation.listArtwork
    }

    var body: some View {
        ZStack {
            background
            content
        }
        .ignoresSafeArea()
        .offset(y: max(0, dragOffset))
        .onTapGesture { toggleControls() }
        // Simultaneous, and with a real minimum distance: added as a plain
        // .gesture it claims the touch sequence and taps never land.
        .simultaneousGesture(dismissDrag)
        .statusBarHidden(!controlsVisible)
        .onAppear {
            following = isTuned
            setScreenAwake(true)
            scheduleHide()
        }
        .onDisappear {
            hideTask?.cancel()
            setScreenAwake(false)
        }
        // Belt and braces: a flag that silently keeps someone's screen awake
        // is a worse bug than anything else on this screen, so it is cleared
        // on backgrounding too rather than trusting onDisappear alone.
        .onChange(of: scenePhase) { _, phase in
            setScreenAwake(phase == .active)
        }
    }

    // MARK: - Layers

    private var background: some View {
        GeometryReader { geo in
            ZStack {
                Color.black
                AsyncImage(url: server.api?.resolve(artworkPath)) { phase in
                    if case .success(let image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                            // Sized explicitly: without a frame the blur has no
                            // bounds to work against and the image escapes the
                            // stack instead of filling it.
                            .frame(width: geo.size.width, height: geo.size.height)
                            .clipped()
                            .blur(radius: 50)
                            .overlay(Color.black.opacity(0.55))
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
            }
        }
    }

    private var content: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)

            // aspectRatio before frame: the other order lets the square grow to
            // whatever height the surrounding spacers leave free, which is a
            // lot on a screen with nothing else on it.
            Artwork(path: artworkPath, api: server.api, cornerRadius: 20)
                .aspectRatio(1, contentMode: .fit)
                .frame(maxWidth: 330, maxHeight: 330)
                .shadow(color: .black.opacity(0.5), radius: 30, y: 12)
                .padding(.horizontal, 32)

            VStack(spacing: 6) {
                // Claims the full width explicitly. Sizing to its content lets
                // the VStack hand it only as much as the text asks for, so it
                // scrolls inside a box narrower than the screen it's on.
                MarqueeText(
                    text: shownNowPlaying?.title ?? shownStation.name,
                    font: .title2,
                    weight: .bold
                )
                .frame(maxWidth: .infinity)
                .foregroundStyle(.white)

                Text(shownNowPlaying?.artist ?? shownStation.description)
                    .font(.title3)
                    .foregroundStyle(.white.opacity(0.75))
                    .lineLimit(1)

                Text(shownStation.name.uppercased())
                    .font(.caption.weight(.semibold))
                    .tracking(1.4)
                    .foregroundStyle(.white.opacity(0.45))
                    .padding(.top, 6)
            }
            .padding(.horizontal, 22)
            .padding(.top, 32)

            Spacer(minLength: 0)

            controls
                .opacity(controlsVisible ? 1 : 0)
                .animation(.easeInOut(duration: 0.35), value: controlsVisible)
                .padding(.bottom, 54)
        }
        .overlay(alignment: .topTrailing) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.85))
                    .frame(width: 38, height: 38)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .buttonStyle(.plain)
            .padding(.top, 60)
            .padding(.trailing, 22)
            .opacity(controlsVisible ? 1 : 0)
            .animation(.easeInOut(duration: 0.35), value: controlsVisible)
            .accessibilityLabel("Close")
        }
    }

    private var controls: some View {
        // Skip / play / skip is symmetric, so the play button stays on the
        // centre line without any balancing. The heart and route sit at the
        // edges, where their presence can't shift it.
        ZStack {
            HStack(spacing: 26) {
                if canSkip {
                    skipButton("backward.end.fill", label: "Previous station") {
                        player.previousStation()
                    }
                }

                StationPlayButton(station: shownStation, size: .largeTitle)
                    .foregroundStyle(.white)
                    .frame(width: 68, height: 68)
                    // .tint must NOT be overridden here: the circle takes its
                    // fill from the ambient tint, so forcing it white first
                    // produced a white circle behind a white glyph.
                    .background(.tint, in: Circle())

                if canSkip {
                    skipButton("forward.end.fill", label: "Next station") {
                        player.nextStation()
                    }
                }
            }

            HStack {
                if following, let hearted = shownNowPlaying?.hearted {
                    Button {
                        player.toggleHeart()
                        scheduleHide()
                    } label: {
                        Image(systemName: hearted ? "heart.fill" : "heart")
                            .font(.system(size: 22, weight: .medium))
                            .foregroundStyle(hearted ? .pink : .white.opacity(0.75))
                            .frame(width: 44, height: 44)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(hearted ? "Remove heart" : "Heart this track")
                }

                Spacer()

                RoutePickerButton(tint: .white.opacity(0.75), activeTint: .white)
                    .frame(width: 28, height: 28)
                    .frame(width: 44, height: 44)
                    .accessibilityLabel("Choose audio output")
            }
            .padding(.horizontal, 22)
        }
        .frame(height: 68)
    }

    /// Only offered while following the player: these move between stations,
    /// and doing that from a station you aren't listening to would be a
    /// surprise rather than a skip.
    private var canSkip: Bool {
        following && player.canSkipStations
    }

    private func skipButton(
        _ systemName: String, label: String, action: @escaping () -> Void
    ) -> some View {
        Button {
            action()
            scheduleHide()
        } label: {
            Image(systemName: systemName)
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(.white.opacity(0.85))
                .frame(width: 48, height: 48)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    // MARK: - Behaviour

    private var dismissDrag: some Gesture {
        DragGesture(minimumDistance: 25)
            .onChanged { value in
                dragOffset = value.translation.height
            }
            .onEnded { value in
                if value.translation.height > 120 {
                    dismiss()
                } else {
                    withAnimation(.spring(duration: 0.3)) { dragOffset = 0 }
                }
            }
    }

    private func toggleControls() {
        withAnimation { controlsVisible.toggle() }
        if controlsVisible { scheduleHide() }
    }

    private func scheduleHide() {
        hideTask?.cancel()
        controlsVisible = true
        hideTask = Task {
            try? await Task.sleep(for: .seconds(8))
            guard !Task.isCancelled else { return }
            withAnimation { controlsVisible = false }
        }
    }

    /// The actual point of this screen — iOS would otherwise sleep half a
    /// minute into an album you're looking at.
    private func setScreenAwake(_ awake: Bool) {
        UIApplication.shared.isIdleTimerDisabled = awake
    }
}
