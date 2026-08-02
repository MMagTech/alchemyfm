import SwiftUI

/// Per-device display preferences, read with `@AppStorage`.
///
/// The content toggles are deliberately independent of the server's
/// `artist_bio_enabled` setting: that one governs every listener on every
/// client, and the common case is wanting this material on a desktop screen but
/// not on a phone. The server toggle still wins in the sense that it stops
/// sending the data at all — these only decide whether to show what arrived.
enum DisplayPreference {
    static let showArtistBio = "alchemyfm.showArtistBio"
    static let showTrackTrivia = "alchemyfm.showTrackTrivia"
    static let appearance = "alchemyfm.appearance"
}

enum AppearanceMode: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }

    /// nil hands the decision back to the system.
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }

    static func resolve(_ raw: String) -> AppearanceMode {
        AppearanceMode(rawValue: raw) ?? .system
    }
}
