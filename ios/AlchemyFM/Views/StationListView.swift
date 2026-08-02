import SwiftUI

struct StationListView: View {
    @Environment(ServerConfig.self) private var server
    @Environment(RadioPlayer.self) private var player

    @State private var store = StationStore()
    @State private var path: [StationSummary] = []
    @State private var showingSettings = false

    var body: some View {
        NavigationStack(path: $path) {
            content
                .navigationTitle("Alchemy FM")
                .navigationDestination(for: StationSummary.self) { station in
                    StationDetailView(station: station)
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
                    if let tuned = player.station {
                        MiniPlayerBar { path.append(tuned) }
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
        // Keeps the lock screen's ⏮/⏭ pointed at the same order shown here.
        .onChange(of: store.stations) { _, stations in
            player.updateStationOrder(stations)
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
            StationRow(station: station) { path.append(station) }
                .listRowBackground(Color.clear)
        }
        .listStyle(.plain)
        .refreshable {
            guard let api = server.api else { return }
            await store.refresh(api: api)
        }
    }
}

/// Two independent controls: the artwork/title area opens the station, the
/// trailing button plays it. Tapping a row must never change what's playing.
struct StationRow: View {
    let station: StationSummary
    let onOpen: () -> Void

    @Environment(ServerConfig.self) private var server
    @Environment(RadioPlayer.self) private var player

    private var isTuned: Bool { player.isTuned(to: station.slug) }

    var body: some View {
        HStack(spacing: 12) {
            Button(action: onOpen) {
                HStack(spacing: 12) {
                    Artwork(path: station.listArtwork, api: server.api)
                        .frame(width: 64, height: 64)

                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(station.name)
                                .font(.headline)
                                .lineLimit(1)
                            if isTuned && player.isPlaying {
                                Image(systemName: "waveform")
                                    .font(.caption)
                                    .foregroundStyle(.tint)
                                    .accessibilityLabel("Now playing")
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
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            StationPlayButton(station: station)
        }
        .padding(.vertical, 4)
    }
}

/// Plays *this* station, whatever is playing now. Only shows a stop control
/// when this is the station currently tuned in.
struct StationPlayButton: View {
    let station: StationSummary
    var size: Font = .title2

    @Environment(RadioPlayer.self) private var player
    @Environment(ServerConfig.self) private var server

    private var isTuned: Bool { player.isTuned(to: station.slug) }
    private var isLive: Bool { isTuned && player.wantsLive }

    var body: some View {
        Button {
            if isTuned {
                player.toggle()
            } else if let api = server.api {
                player.tune(to: station, api: api)
            }
        } label: {
            ZStack {
                if isTuned && player.isBusy {
                    ProgressView()
                } else {
                    Image(systemName: isLive ? "stop.fill" : "play.fill")
                        .font(size)
                }
            }
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isLive ? "Stop \(station.name)" : "Play \(station.name)")
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
                // The bar is always on screen and has no detail view of its
                // own, so a truncated title here has nowhere else to be read.
                MarqueeText(
                    text: player.nowPlaying?.title ?? player.station?.name ?? "",
                    font: .subheadline,
                    weight: .medium
                )
                MarqueeText(text: subtitle, font: .caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 0)

            if let station = player.station {
                StationPlayButton(station: station)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
    }

    private var subtitle: String {
        guard player.isPlaying else { return player.statusText }
        let station = player.station?.name ?? ""
        let artist = player.nowPlaying?.artist ?? ""
        return artist.isEmpty ? station : "\(artist) · \(station)"
    }
}
