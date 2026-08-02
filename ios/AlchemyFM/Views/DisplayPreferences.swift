import Foundation

/// Per-device display preferences, read with `@AppStorage`.
///
/// Deliberately independent of the server's `artist_bio_enabled` setting: that
/// one governs every listener on every client, and the common case is wanting
/// this material on a desktop screen but not on a phone. The server toggle
/// still wins in the sense that it stops sending the data at all — these only
/// decide whether to show what did arrive.
enum DisplayPreference {
    static let showArtistBio = "alchemyfm.showArtistBio"
    static let showTrackTrivia = "alchemyfm.showTrackTrivia"
}
