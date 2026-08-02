import Foundation
import Observation

/// The backend base URL. Alchemy FM is self-hosted, so there is no default —
/// first run asks for the address.
@MainActor
@Observable
final class ServerConfig {
    private static let defaultsKey = "alchemyfm.serverURL"

    private(set) var baseURL: URL?

    /// The accent this server tells its listeners to use, so the app can match
    /// the web player by default instead of picking its own colour.
    private(set) var serverTheme: String?

    func refreshServerTheme() async {
        guard let api else { return }
        serverTheme = try? await api.health().defaultTheme
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let stored = defaults.string(forKey: Self.defaultsKey) {
            baseURL = URL(string: stored)
        }
    }

    private let defaults: UserDefaults

    var isConfigured: Bool { baseURL != nil }

    var api: AlchemyAPI? {
        baseURL.map { AlchemyAPI(baseURL: $0) }
    }

    /// Display form for the settings field — no trailing slash noise.
    var displayString: String {
        baseURL?.absoluteString.trimmingTrailingSlash ?? ""
    }

    func save(_ url: URL) {
        baseURL = url
        defaults.set(url.absoluteString, forKey: Self.defaultsKey)
    }

    func clear() {
        baseURL = nil
        defaults.removeObject(forKey: Self.defaultsKey)
    }

    /// Turns whatever the user typed into a usable base URL.
    /// `alchemy.local:8080` and `http://10.0.0.5:8080/` both normalise fine.
    static func normalize(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "http://" + text }
        text = text.trimmingTrailingSlash

        guard let components = URLComponents(string: text),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = components.host, !host.isEmpty
        else { return nil }

        // A base URL must end in "/" for relative-path resolution to keep any
        // subpath (e.g. https://host/radio/ + api/health).
        return URL(string: text + "/")
    }

    /// Confirms a candidate URL is actually an Alchemy FM server before saving.
    static func validate(_ url: URL) async throws -> ServerHealth {
        try await AlchemyAPI(baseURL: url).health()
    }
}

extension String {
    var trimmingTrailingSlash: String {
        var copy = self
        while copy.hasSuffix("/") { copy.removeLast() }
        return copy
    }
}
