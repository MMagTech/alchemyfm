import Foundation
import GoogleCast
import Observation

/// Chromecast session state.
///
/// While a session is live the Chromecast fetches the Icecast stream itself and
/// the phone is only a remote — so `RadioPlayer`'s AVPlayer, its stall watchdog
/// and its reconnect logic all stand down, and recovery becomes the receiver's
/// problem. Everything above playback (station list, now-playing polling,
/// favourites, hearts) is unaffected, because none of it depends on where the
/// audio comes out.
@MainActor
@Observable
final class CastController: NSObject {
    static let shared = CastController()

    private(set) var isCasting = false
    private(set) var deviceName: String?

    /// Whether any receiver is currently visible on the network. Drives whether
    /// the cast button is worth showing at all.
    private(set) var hasDevices = false

    @ObservationIgnored private var started = false
    @ObservationIgnored private var discoveryObservation: NSKeyValueObservation?

    /// Must run before any Cast UI is created.
    func start() {
        guard !started else { return }
        started = true

        // The Default Media Receiver plays plain audio without a custom
        // receiver app of our own. It is also the id named in Info.plist's
        // NSBonjourServices entry, which has to match for discovery to work.
        let criteria = GCKDiscoveryCriteria(applicationID: kGCKDefaultMediaReceiverApplicationID)
        let options = GCKCastOptions(discoveryCriteria: criteria)
        options.physicalVolumeButtonsWillControlDeviceVolume = true
        // Keep the session alive when the app is backgrounded — the whole point
        // is that the speakers keep playing.
        options.suspendSessionsWhenBackgrounded = false
        // Defaults to true, which defers discovery until a cast button is
        // tapped. Since the button here only appears once a device has been
        // found, that deadlocks: no button, so no scan, so no devices, so no
        // button — and the local network prompt never fires either, because
        // nothing ever touches the network.
        options.startDiscoveryAfterFirstTapOnCastButton = false
        GCKCastContext.setSharedInstanceWith(options)

        GCKCastContext.sharedInstance().sessionManager.add(self)

        let discovery = GCKCastContext.sharedInstance().discoveryManager
        discovery.add(self)
        // Active, not passive: passive scans are lower power but far slower to
        // notice a device, which reads as "casting doesn't work".
        discovery.passiveScan = false
        discovery.startDiscovery()
        hasDevices = discovery.deviceCount > 0
    }

    var remoteMediaClient: GCKRemoteMediaClient? {
        GCKCastContext.sharedInstance().sessionManager.currentCastSession?.remoteMediaClient
    }

    /// Hands a station to the receiver. The Chromecast pulls the stream from
    /// the server directly, so this only has to describe it.
    func load(streamURL: URL, title: String, artist: String, station: String, artwork: URL?) {
        guard let client = remoteMediaClient else { return }

        let metadata = GCKMediaMetadata(metadataType: .musicTrack)
        metadata.setString(title, forKey: kGCKMetadataKeyTitle)
        metadata.setString(artist, forKey: kGCKMetadataKeyArtist)
        metadata.setString(station, forKey: kGCKMetadataKeyAlbumTitle)
        if let artwork {
            metadata.addImage(GCKImage(url: artwork, width: 300, height: 300))
        }

        let media = GCKMediaInformationBuilder(contentURL: streamURL)
        // Live, so the receiver shows no scrubber and doesn't try to seek.
        media.streamType = .live
        media.contentType = "audio/mpeg"
        media.metadata = metadata

        let request = GCKMediaLoadRequestDataBuilder()
        request.mediaInformation = media.build()
        request.autoplay = true

        client.loadMedia(with: request.build())
    }

    /// Updates what the receiver displays without interrupting playback.
    func updateMetadata(title: String, artist: String, station: String, artwork: URL?) {
        // The Default Media Receiver takes its display metadata from the load
        // request, so refreshing it means re-issuing one — which would restart
        // the stream. Not worth a gap in audio every time a track changes; the
        // receiver keeps showing the track it started on.
    }

    /// Stops the audio but keeps the session, so the next station can start
    /// on the same speakers without reconnecting to them.
    func stopPlayback() {
        remoteMediaClient?.stop()
    }

    /// Disconnects from the receiver entirely.
    func endSession() {
        remoteMediaClient?.stop()
        GCKCastContext.sharedInstance().sessionManager.endSession()
    }
}

extension CastController: GCKSessionManagerListener {
    nonisolated func sessionManager(
        _ sessionManager: GCKSessionManager, didStart session: GCKCastSession
    ) {
        Task { @MainActor in
            self.isCasting = true
            self.deviceName = session.device.friendlyName
        }
    }

    nonisolated func sessionManager(
        _ sessionManager: GCKSessionManager, didResumeCastSession session: GCKCastSession
    ) {
        Task { @MainActor in
            self.isCasting = true
            self.deviceName = session.device.friendlyName
        }
    }

    nonisolated func sessionManager(
        _ sessionManager: GCKSessionManager, didEnd session: GCKCastSession, withError error: Error?
    ) {
        Task { @MainActor in
            self.isCasting = false
            self.deviceName = nil
        }
    }

    nonisolated func sessionManager(
        _ sessionManager: GCKSessionManager,
        didFailToStart session: GCKCastSession,
        withError error: Error
    ) {
        Task { @MainActor in
            self.isCasting = false
            self.deviceName = nil
        }
    }
}

extension CastController: GCKDiscoveryManagerListener {
    nonisolated func didUpdateDeviceList() {
        Task { @MainActor in
            self.hasDevices = GCKCastContext.sharedInstance().discoveryManager.deviceCount > 0
        }
    }
}
