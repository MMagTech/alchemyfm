import AVFoundation
import Foundation
import MediaPlayer
import Observation
import UIKit

/// Owns the live stream: one `AVPlayer`, the audio session, the lock-screen
/// controls, the now-playing poll loop, and — the whole point of the native
/// app — reconnect logic that keeps working while backgrounded.
///
/// Alchemy FM is live radio, not a music library: there is no seeking, no
/// skipping, and "play" always means *re-tune to live*, never *resume a
/// buffer*. See docs/IOS_APP_HANDOFF.md.
@MainActor
@Observable
final class RadioPlayer {

    enum State: Equatable {
        case idle
        case connecting
        case playing
        case reconnecting(attempt: Int)
        case interrupted
        case failed(String)
    }

    private(set) var state: State = .idle
    private(set) var station: StationSummary?
    private(set) var detail: StationDetail?

    /// True while the user wants audio. Drives the watchdog and every reconnect
    /// decision — nothing retries once the user has actually stopped.
    private(set) var wantsLive = false

    @ObservationIgnored var api: AlchemyAPI?

    /// The station list in display order, kept current by the station list
    /// screen. Backs the lock screen's ⏮/⏭, which tune between stations.
    @ObservationIgnored private var stationOrder: [StationSummary] = []

    func updateStationOrder(_ stations: [StationSummary]) {
        stationOrder = stations
    }

    // MARK: Private state

    @ObservationIgnored private var player: AVPlayer?
    @ObservationIgnored private var observations: [NSKeyValueObservation] = []
    @ObservationIgnored private var notificationObservers: [NSObjectProtocol] = []
    @ObservationIgnored private var reconnectTask: Task<Void, Never>?
    @ObservationIgnored private var watchdogTask: Task<Void, Never>?
    @ObservationIgnored private var pollTask: Task<Void, Never>?
    @ObservationIgnored private var artworkTask: Task<Void, Never>?
    @ObservationIgnored private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    @ObservationIgnored private var attempt = 0
    @ObservationIgnored private var playingEpoch = 0
    @ObservationIgnored private var lastProgressMarker: Double?
    @ObservationIgnored private var frozenSamples = 0
    @ObservationIgnored private var currentArtworkKey: String?
    @ObservationIgnored private var remoteCommandsInstalled = false
    @ObservationIgnored private var isInterrupted = false

    // MARK: - Derived state for the UI

    var isPlaying: Bool { state == .playing }

    var isBusy: Bool {
        switch state {
        case .connecting, .reconnecting: return true
        default: return false
        }
    }

    var nowPlaying: NowPlaying? { detail?.nowPlaying ?? station?.nowPlaying }

    var statusText: String {
        switch state {
        case .idle: return "Stopped"
        case .connecting: return "Tuning in…"
        case .playing: return "Live"
        case .reconnecting(let n): return n <= 1 ? "Reconnecting…" : "Reconnecting… (\(n))"
        case .interrupted: return "Interrupted"
        case .failed(let message): return message
        }
    }

    func isTuned(to slug: String) -> Bool { station?.slug == slug }

    // MARK: - Tuning

    func tune(to station: StationSummary, api: AlchemyAPI) {
        self.api = api
        if self.station?.slug != station.slug {
            teardownPlayer()
            pollTask?.cancel()
            pollTask = nil
            detail = nil
        }
        self.station = station
        installRemoteCommands()
        startPolling()
        play()
    }

    func play() {
        guard station != nil else { return }
        wantsLive = true
        isInterrupted = false
        attempt = 0
        cancelReconnect()
        openStream()
    }

    func stop() {
        wantsLive = false
        isInterrupted = false
        cancelReconnect()
        teardownPlayer()
        state = .idle
        deactivateSession()
        endBackgroundTask()
        updateNowPlayingInfo()
    }

    func toggle() {
        if wantsLive { stop() } else { play() }
    }

    /// Fully leaves the station — stops audio and drops the poll loop.
    func leave() {
        stop()
        pollTask?.cancel()
        pollTask = nil
        station = nil
        detail = nil
        currentArtworkKey = nil
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    // MARK: - Stream lifecycle

    private func openStream() {
        guard let station else { return }
        playingEpoch = detail?.streamEpoch ?? station.streamEpoch
        guard let url = streamURL(for: station) else {
            state = .failed("This station has no stream URL.")
            return
        }

        teardownPlayer()
        state = attempt == 0 ? .connecting : .reconnecting(attempt: attempt)
        activateSession()

        let item = AVPlayerItem(url: url)
        let player = AVPlayer(playerItem: item)
        // Left at its default (true) on purpose. Setting it false means a
        // `play()` issued before the item reaches `.readyToPlay` is dropped and
        // never retried — the player then buffers forever at rate 0, which
        // looks exactly like a working connection that produces no sound.
        player.allowsExternalPlayback = true
        self.player = player

        observe(player: player, item: item)
        player.play()

        lastProgressMarker = nil
        frozenSamples = 0
        startWatchdog()
        updateNowPlayingInfo()
    }

    /// Cache-busts every connection. The web player uses `stream_epoch` for
    /// this; the extra nonce covers repeat attempts *within* one epoch, where an
    /// unchanged URL can come back from a stale cached connection.
    private func streamURL(for station: StationSummary) -> URL? {
        // Falls back to the direct Icecast URL only if we somehow have no API
        // client — see `listenURL` for why that is the worse option on iOS.
        let candidate = api?.listenURL(slug: station.slug)
            ?? api?.resolve(station.streamUrl)
            ?? URL(string: station.streamUrl)
        guard let base = candidate else {
            return nil
        }
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: true) else {
            return base
        }
        var query = components.queryItems ?? []
        query.append(URLQueryItem(name: "_e", value: String(playingEpoch)))
        query.append(URLQueryItem(name: "_t", value: String(Int(Date().timeIntervalSince1970))))
        components.queryItems = query
        return components.url ?? base
    }

    private func teardownPlayer() {
        // Observers first: tearing down otherwise fires a spurious `.paused`
        // transition that would look like a stall and trigger a reconnect.
        observations.forEach { $0.invalidate() }
        observations.removeAll()
        notificationObservers.forEach { NotificationCenter.default.removeObserver($0) }
        notificationObservers.removeAll()
        watchdogTask?.cancel()
        watchdogTask = nil

        player?.pause()
        player?.replaceCurrentItem(with: nil)
        player = nil
        lastProgressMarker = nil
        frozenSamples = 0
    }

    // MARK: - Observation

    private func observe(player: AVPlayer, item: AVPlayerItem) {
        observations = [
            item.observe(\.status, options: [.new]) { [weak self] item, _ in
                Task { @MainActor [weak self] in self?.handleItemStatus(item) }
            },
            player.observe(\.timeControlStatus, options: [.new]) { [weak self] player, _ in
                Task { @MainActor [weak self] in self?.handleTimeControlStatus(player) }
            },
        ]

        let center = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()
        notificationObservers = [
            center.addObserver(
                forName: AVPlayerItem.playbackStalledNotification, object: item, queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in self?.scheduleReconnect(reason: "playback stalled") }
            },
            center.addObserver(
                forName: AVPlayerItem.failedToPlayToEndTimeNotification, object: item, queue: .main
            ) { [weak self] note in
                let error = note.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
                let reason = error?.localizedDescription ?? "stream ended"
                Task { @MainActor [weak self] in self?.scheduleReconnect(reason: reason) }
            },
            center.addObserver(
                forName: AVAudioSession.interruptionNotification, object: session, queue: .main
            ) { [weak self] note in
                Task { @MainActor [weak self] in self?.handleInterruption(note) }
            },
            center.addObserver(
                forName: AVAudioSession.routeChangeNotification, object: session, queue: .main
            ) { [weak self] note in
                Task { @MainActor [weak self] in self?.handleRouteChange(note) }
            },
        ]
    }

    private func handleItemStatus(_ item: AVPlayerItem) {
        guard item === player?.currentItem, item.status == .failed else { return }
        scheduleReconnect(reason: item.error?.localizedDescription ?? "stream failed")
    }

    private func handleTimeControlStatus(_ player: AVPlayer) {
        guard player === self.player else { return }
        switch player.timeControlStatus {
        case .playing:
            attempt = 0
            state = .playing
            endBackgroundTask()
            updateNowPlayingInfo()
        case .paused:
            // We never pause ourselves without first tearing down observers, so
            // reaching here while live means iOS or the network stopped us.
            if wantsLive, !isInterrupted {
                scheduleReconnect(reason: "playback stopped unexpectedly")
            }
        case .waitingToPlayAtSpecifiedRate:
            break
        @unknown default:
            break
        }
    }

    private func handleInterruption(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw)
        else { return }

        switch type {
        case .began:
            // A call or Siri took the session. Suspend our own retry machinery
            // so we aren't fighting the system for it.
            isInterrupted = true
            cancelReconnect()
            watchdogTask?.cancel()
            watchdogTask = nil
            state = .interrupted
        case .ended:
            isInterrupted = false
            let rawOptions = note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            let options = AVAudioSession.InterruptionOptions(rawValue: rawOptions)
            if wantsLive, options.contains(.shouldResume) {
                play()
            } else {
                stop()
            }
        @unknown default:
            break
        }
    }

    private func handleRouteChange(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              let reason = AVAudioSession.RouteChangeReason(rawValue: raw)
        else { return }
        // Headphones pulled / Bluetooth dropped — never fall back to the
        // speaker at whatever volume the room didn't ask for.
        if reason == .oldDeviceUnavailable { stop() }
    }

    // MARK: - Recovery

    /// Catches the failure mode AVPlayer doesn't report: it still claims to be
    /// playing, but no audio is actually arriving. This is the native
    /// replacement for the PWA's `setTimeout` heartbeat, and unlike that one it
    /// keeps running while backgrounded.
    private func startWatchdog() {
        watchdogTask?.cancel()
        watchdogTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard !Task.isCancelled, let self else { return }
                self.checkForSilentStall()
            }
        }
    }

    /// A number that must keep rising while the stream is healthy.
    ///
    /// Deliberately *not* `player.currentTime()`: on an Icecast stream the
    /// playhead doesn't advance the way a finite asset's does, so treating a
    /// static currentTime as a stall tears down a perfectly good connection
    /// every few seconds. Bytes transferred is the direct measure of the thing
    /// this check is actually named for.
    private func streamProgressMarker() -> Double? {
        guard let item = player?.currentItem else { return nil }
        // Summed across every event, not just the last one: byte counts are
        // per-event, so reading only the newest entry makes the total appear to
        // drop each time AVPlayer opens a new one — which reads as a stall on a
        // stream that is in fact perfectly healthy.
        if let events = item.accessLog()?.events, !events.isEmpty {
            let total = events.reduce(Int64(0)) { $0 + max(0, $1.numberOfBytesTransferred) }
            if total > 0 { return Double(total) }
        }
        // Before the access log has an entry, the buffered range still grows.
        if let range = item.loadedTimeRanges.last?.timeRangeValue {
            return CMTimeGetSeconds(CMTimeRangeGetEnd(range))
        }
        return nil
    }

    private func checkForSilentStall() {
        guard wantsLive, state == .playing else {
            frozenSamples = 0
            return
        }
        guard let marker = streamProgressMarker() else {
            frozenSamples = 0
            lastProgressMarker = nil
            return
        }
        defer { lastProgressMarker = marker }

        guard let previous = lastProgressMarker else {
            frozenSamples = 0
            return
        }
        if marker > previous {
            frozenSamples = 0
            return
        }
        frozenSamples += 1
        // ~6s of a live stream delivering nothing while it claims to be playing.
        if frozenSamples >= 3 {
            // Worth logging the playhead too: a frozen marker with a moving
            // playhead means the stream died, while both frozen at zero means
            // the player never actually started (see openStream).
            NSLog(
                "[AlchemyFM] stall: marker=%.0f playhead=%.1f rate=%.1f",
                marker, CMTimeGetSeconds(player?.currentTime() ?? .zero), player?.rate ?? -1
            )
            scheduleReconnect(reason: "no audio arriving")
        }
    }

    private func scheduleReconnect(reason: String) {
        guard wantsLive, !isInterrupted, reconnectTask == nil else { return }

        // Buys ~30s of execution time so a reconnect that starts while the
        // screen is locked actually gets to finish before iOS suspends us.
        beginBackgroundTask()

        attempt += 1
        state = .reconnecting(attempt: attempt)
        // Mirrors the web player's backoff: 2s, 4s, 6s … capped at 15s.
        let delay = min(15.0, 2.0 + Double(attempt - 1) * 2.0)
        NSLog("[AlchemyFM] reconnect in %.0fs (attempt %d): %@", delay, attempt, reason)

        teardownPlayer()
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled, let self else { return }
            self.reconnectTask = nil
            guard self.wantsLive, !self.isInterrupted else { return }
            self.openStream()
        }
    }

    private func cancelReconnect() {
        reconnectTask?.cancel()
        reconnectTask = nil
    }

    // MARK: - Audio session

    private func activateSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .default, policy: .longFormAudio)
            try session.setActive(true)
        } catch {
            NSLog("[AlchemyFM] audio session activation failed: %@", error.localizedDescription)
        }
    }

    private func deactivateSession() {
        try? AVAudioSession.sharedInstance().setActive(
            false, options: .notifyOthersOnDeactivation
        )
    }

    private func beginBackgroundTask() {
        guard backgroundTask == .invalid else { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "AlchemyFM.reconnect") {
            Task { @MainActor [weak self] in self?.endBackgroundTask() }
        }
    }

    private func endBackgroundTask() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    // MARK: - Now-playing metadata

    /// The API — not Icecast in-stream metadata — is the source of truth.
    private func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let api = self.api, let slug = self.station?.slug else { return }
                do {
                    let detail = try await api.station(slug: slug)
                    if !Task.isCancelled { self.apply(detail) }
                } catch {
                    // Transient by assumption — the loop itself is the retry.
                }
                guard !Task.isCancelled else { return }
                try? await Task.sleep(for: .milliseconds(self.pollInterval))
            }
        }
    }

    private var pollInterval: Int {
        guard wantsLive else { return 5000 }
        // Foreground mirrors the web player's 750ms now-playing poll. In the
        // background the only consumer is the lock screen, which doesn't need
        // sub-second updates — and a 750ms network loop behind a locked screen
        // is a battery cost with nothing to show for it.
        return UIApplication.shared.applicationState == .active ? 750 : 3000
    }

    private func apply(_ detail: StationDetail) {
        self.detail = detail
        station = detail.summary
        updateNowPlayingInfo()

        // The broadcast restarted underneath us: our connection is dead even if
        // AVPlayer hasn't worked that out yet.
        if wantsLive, detail.streamEpoch != playingEpoch {
            playingEpoch = detail.streamEpoch
            scheduleReconnect(reason: "stream epoch changed")
        }
    }

    // MARK: - Lock screen / Control Center

    private func installRemoteCommands() {
        guard !remoteCommandsInstalled else { return }
        remoteCommandsInstalled = true

        let center = MPRemoteCommandCenter.shared()
        center.playCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.play() }
            return .success
        }
        // Live radio has nothing to resume into, so "pause" is really "stop".
        center.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.stop() }
            return .success
        }
        center.stopCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.stop() }
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.toggle() }
            return .success
        }

        // There is no next *track* to skip to — everyone hears the same
        // broadcast — so these tune to the next station instead, which is the
        // closest thing a radio has to a skip button.
        center.nextTrackCommand.isEnabled = true
        center.nextTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.tuneToAdjacentStation(offset: 1) }
            return .success
        }
        center.previousTrackCommand.isEnabled = true
        center.previousTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.tuneToAdjacentStation(offset: -1) }
            return .success
        }

        // Seeking stays off: a live stream has no timeline to scrub.
        let unsupported: [MPRemoteCommand] = [
            center.skipForwardCommand, center.skipBackwardCommand,
            center.seekForwardCommand, center.seekBackwardCommand,
            center.changePlaybackPositionCommand,
        ]
        unsupported.forEach { $0.isEnabled = false }
    }

    /// Wraps around, so ⏭ off the end of the list returns to the first station
    /// rather than doing nothing and looking broken.
    private func tuneToAdjacentStation(offset: Int) {
        guard let api,
              !stationOrder.isEmpty,
              let current = station,
              let index = stationOrder.firstIndex(where: { $0.slug == current.slug })
        else { return }

        let count = stationOrder.count
        let next = stationOrder[((index + offset) % count + count) % count]
        guard next.slug != current.slug else { return }
        tune(to: next, api: api)
    }

    private func updateNowPlayingInfo() {
        let center = MPNowPlayingInfoCenter.default()
        guard let station else {
            center.nowPlayingInfo = nil
            return
        }
        let track = nowPlaying
        var info = center.nowPlayingInfo ?? [:]
        info[MPMediaItemPropertyTitle] = track?.title ?? station.name
        info[MPMediaItemPropertyArtist] = track?.artist ?? station.description
        info[MPMediaItemPropertyAlbumTitle] = station.name
        info[MPNowPlayingInfoPropertyIsLiveStream] = true
        info[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? 1.0 : 0.0
        center.nowPlayingInfo = info
        refreshArtworkIfNeeded()
    }

    private func refreshArtworkIfNeeded() {
        guard let api, let station else { return }
        guard let url = api.resolve(nowPlaying?.coverUrl) ?? api.resolve(station.artworkUrl) else {
            return
        }
        let key = url.absoluteString
        guard key != currentArtworkKey else { return }
        currentArtworkKey = key

        artworkTask?.cancel()
        artworkTask = Task { [weak self] in
            guard let data = try? await api.imageData(at: url),
                  let image = UIImage(data: data),
                  !Task.isCancelled,
                  let self, self.currentArtworkKey == key
            else { return }
            var info = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
            info[MPMediaItemPropertyArtwork] = MPMediaItemArtwork(boundsSize: image.size) { _ in
                image
            }
            MPNowPlayingInfoCenter.default().nowPlayingInfo = info
        }
    }
}
