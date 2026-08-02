import Foundation

enum APIError: LocalizedError {
    case badURL
    case http(status: Int)
    case notAlchemyServer

    var errorDescription: String? {
        switch self {
        case .badURL:
            return "That doesn't look like a valid address."
        case .http(let status) where status == 404:
            return "Not found on this server (404)."
        case .http(let status):
            return "The server returned HTTP \(status)."
        case .notAlchemyServer:
            return "Reachable, but it doesn't look like an Alchemy FM server."
        }
    }
}

struct AlchemyAPI: Sendable {
    let baseURL: URL

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 20
        // Deliberately off: with it on, an unreachable server doesn't fail, it
        // waits — which leaves the setup screen spinning forever on a typo'd
        // address, and stalls the poll loop instead of letting it retry.
        config.waitsForConnectivity = false
        // Station/now-playing responses are all `Cache-Control: no-store`
        // server-side; skipping the local cache avoids a redundant disk hit on
        // a 750ms poll loop.
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        // FastAPI emits naive ISO-8601 for `played_at`, sometimes with
        // fractional seconds, sometimes with a timezone. A malformed or
        // unexpected timestamp must not fail the whole station payload.
        d.dateDecodingStrategy = .custom { decoder in
            let raw = try decoder.singleValueContainer().decode(String.self)
            return Self.parseTimestamp(raw) ?? .distantPast
        }
        return d
    }()

    private static let timestampFormatters: [ISO8601DateFormatter] = {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return [withFraction, plain]
    }()

    private static func parseTimestamp(_ raw: String) -> Date? {
        // Naive timestamps (no trailing Z/offset) are UTC on this backend.
        let candidates = raw.hasSuffix("Z") || raw.contains("+") ? [raw] : [raw + "Z", raw]
        for candidate in candidates {
            for formatter in timestampFormatters {
                if let date = formatter.date(from: candidate) { return date }
            }
        }
        return nil
    }

    // MARK: - Endpoints

    func stations() async throws -> [StationSummary] {
        try await get("/api/stations")
    }

    func station(slug: String) async throws -> StationDetail {
        let escaped = slug.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? slug
        return try await get("/api/stations/\(escaped)")
    }

    func health() async throws -> ServerHealth {
        let health: ServerHealth = try await get("/api/health")
        guard health.status == "ok" else { throw APIError.notAlchemyServer }
        return health
    }

    /// The stream URL to hand `AVPlayer`.
    ///
    /// Deliberately the backend's same-origin `/listen` endpoint rather than
    /// the direct Icecast `stream_url`. Before opening a real playback
    /// connection, iOS sniffs a media resource with a tiny `Range: bytes=0-1`
    /// probe. Icecast ignores the range and answers with an endless `200`, so
    /// the probe never terminates and playback never starts — the connection
    /// registers as a listener while producing no audio. `/listen` answers the
    /// probe with a bounded `206` (see `_probe_range_end` in
    /// backend/app/routers/stations.py) and streams normally otherwise.
    func listenURL(slug: String) -> URL? {
        let escaped = slug.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? slug
        return URL(string: "/api/stations/\(escaped)/listen", relativeTo: baseURL)?.absoluteURL
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw APIError.badURL }
        let (data, response) = try await Self.session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw APIError.http(status: http.statusCode)
        }
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch is DecodingError {
            // Reaching a live web server that isn't Alchemy FM is the common
            // case here (a router admin page, a Traefik 404 page, …).
            throw APIError.notAlchemyServer
        }
    }

    // MARK: - URL helpers

    /// Resolves a URL the API handed us. `artwork_url` and `stream_url` arrive
    /// absolute (built from the request host), but `cover_url` is a same-origin
    /// path like `/api/cover/abc?size=300`, so both forms have to work.
    func resolve(_ value: String?) -> URL? {
        guard let value, !value.isEmpty else { return nil }
        if value.hasPrefix("http://") || value.hasPrefix("https://") {
            return URL(string: value)
        }
        return URL(string: value, relativeTo: baseURL)?.absoluteURL
    }

    /// Fetches image bytes (cover art, station artwork) off the shared session.
    func imageData(at url: URL) async throws -> Data {
        let (data, response) = try await Self.session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw APIError.http(status: http.statusCode)
        }
        return data
    }
}
