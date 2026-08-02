import SwiftUI

struct StationListView: View {
    @Environment(ServerConfig.self) private var server
    @Environment(RadioPlayer.self) private var player

    @State private var store = StationStore()
    @State private var path: [String] = []
    @State private var showingSettings = false

    var body: some View {
        NavigationStack(path: $path) {
            content
                .navigationTitle("Alchemy FM")
                .navigationDestination(for: String.self) { _ in
                    NowPlayingView()
                }
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            showingSettings = true
                        } label: {
                            Image(systemName: "gearshape")
                        }
                        .accessibilityLabel("Settings")
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    if player.station != nil && path.isEmpty {
                        MiniPlayerBar { path.append("now-playing") }
                    }
                }
        }
        .sheet(isPresented: $showingSettings) {
            SettingsView()
        }
        .task(id: server.baseURL) {
            store.reset()
            guard let api = server.api else { return }
            store.start(api: api)
        }
        .onDisappear { store.stop() }
    }

    @ViewBuilder
    private var content: some View {
        if let message = store.errorMessage, store.stations.isEmpty {
            ContentUnavailableView {
                Label("Can't reach the server", systemImage: "wifi.exclamationmark")
            } description: {
                Text(message)
            } actions: {
                Button("Server settings") { showingSettings = true }
            }
        } else if store.stations.isEmpty && store.hasLoaded {
            ContentUnavailableView(
                "No stations",
                systemImage: "antenna.radiowaves.left.and.right.slash",
                description: Text("This server has no enabled stations yet.")
            )
        } else if store.stations.isEmpty {
            ProgressView().controlSize(.large)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            stationList
        }
    }

    private var stationList: some View {
        List(store.stations) { station in
            Button {
                tune(to: station)
            } label: {
                StationRow(station: station, isTuned: player.isTuned(to: station.slug))
            }
            .buttonStyle(.plain)
            .listRowBackground(Color.clear)
        }
        .listStyle(.plain)
        .refreshable {
            guard let api = server.api else { return }
            await store.refresh(api: api)
        }
    }

    private func tune(to station: StationSummary) {
        guard let api = server.api else { return }
        player.tune(to: station, api: api)
        path.append("now-playing")
    }
}

struct StationRow: View {
    let station: StationSummary
    let isTuned: Bool

    @Environment(ServerConfig.self) private var server

    var body: some View {
        HStack(spacing: 12) {
            Artwork(path: station.artworkUrl, api: server.api)
                .frame(width: 64, height: 64)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(station.name)
                        .font(.headline)
                        .lineLimit(1)
                    if isTuned {
                        Image(systemName: "waveform")
                            .font(.caption)
                            .foregroundStyle(.tint)
                    }
                }

                if let track = station.nowPlaying {
                    Text("\(track.title) — \(track.artist)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                } else if !station.description.isEmpty {
                    Text(station.description)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                OnAirBadge(isOnAir: station.onAir, listeners: station.listeners)
            }

            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

struct MiniPlayerBar: View {
    @Environment(RadioPlayer.self) private var player
    @Environment(ServerConfig.self) private var server

    let onTap: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Artwork(path: player.nowPlaying?.coverUrl ?? player.station?.artworkUrl,
                    api: server.api,
                    cornerRadius: 6)
                .frame(width: 40, height: 40)

            VStack(alignment: .leading, spacing: 2) {
                Text(player.nowPlaying?.title ?? player.station?.name ?? "")
                    .font(.subheadline.weight(.medium))
                    .lineLimit(1)
                Text(player.isPlaying ? (player.nowPlaying?.artist ?? "") : player.statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)

            PlayStopButton()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
    }
}

/// Live radio: stop, not pause. Restarting always re-tunes to live.
struct PlayStopButton: View {
    @Environment(RadioPlayer.self) private var player
    var size: Font = .title2

    var body: some View {
        Button {
            player.toggle()
        } label: {
            ZStack {
                if player.isBusy {
                    ProgressView()
                } else {
                    Image(systemName: player.wantsLive ? "stop.fill" : "play.fill")
                        .font(size)
                }
            }
            .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(player.wantsLive ? "Stop" : "Play")
    }
}
