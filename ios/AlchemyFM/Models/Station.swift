import Foundation

// Mirrors backend/app/schemas.py. Only the listener-facing fields are modelled —
// Codable ignores everything else the API sends, so admin-only fields can be
// added server-side without breaking the app.

struct KnowledgeSource: Codable, Hashable {
    let url: String
    var title: String = ""
}

struct KnowledgeFact: Codable, Hashable {
    let category: String
    let text: String
    var confidence: Double = 0
    var sources: [KnowledgeSource] = []
}

struct KnowledgeBlock: Codable, Hashable {
    var facts: [KnowledgeFact] = []
    var rotationIntervalSec: Int = 15
}

struct NowPlaying: Codable, Hashable {
    let title: String
    let artist: String
    var itemId: String?
    var coverUrl: String?
    var artistBio: String?
    var artistBioUrl: String?
    var knowledge: KnowledgeBlock?

    /// Identity for "did the track change?" checks. `item_id` is the real key
    /// when present; title+artist covers stations whose source has no ids.
    var trackKey: String {
        itemId ?? "\(artist)|\(title)"
    }
}

struct TrackRef: Codable, Hashable {
    let itemId: String
    let title: String
    let artist: String
    var coverUrl: String?
    /// Only set on recently-played rows.
    var playedAt: Date?
}

/// `GET /api/stations`
struct StationSummary: Codable, Hashable, Identifiable {
    let slug: String
    let name: String
    var description: String = ""
    var artworkUrl: String = ""
    /// Direct Icecast URL. Preferred over the `/listen` redirect for playback —
    /// it skips the backend proxy hop and its 20/min rate limit.
    var streamUrl: String = ""
    var listeners: Int = 0
    var onAir: Bool = false
    /// Bumped when the broadcast restarts; used to cache-bust reconnects.
    var streamEpoch: Int = 0
    var nowPlaying: NowPlaying?
    var featured: Bool = false

    var id: String { slug }
}

/// `GET /api/stations/{slug}`
struct StationDetail: Codable, Hashable, Identifiable {
    let slug: String
    let name: String
    var description: String = ""
    var artworkUrl: String = ""
    var streamUrl: String = ""
    var listeners: Int = 0
    var onAir: Bool = false
    var streamEpoch: Int = 0
    var nowPlaying: NowPlaying?
    var upNext: [TrackRef] = []
    var recentlyPlayed: [TrackRef] = []

    var id: String { slug }

    var summary: StationSummary {
        StationSummary(
            slug: slug,
            name: name,
            description: description,
            artworkUrl: artworkUrl,
            streamUrl: streamUrl,
            listeners: listeners,
            onAir: onAir,
            streamEpoch: streamEpoch,
            nowPlaying: nowPlaying
        )
    }
}

/// `GET /api/health` — used to validate a server URL before saving it.
struct ServerHealth: Codable {
    let status: String
    var stationsEnabled: Int = 0
    var encodeFormat: String = "mp3"
    var bitrate: Int = 0
    var gitSha: String = "unknown"
}
