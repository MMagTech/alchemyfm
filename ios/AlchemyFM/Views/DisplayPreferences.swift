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
    static let accent = "alchemyfm.accent"
}

/// The same six accents the web player offers, with the same hex values —
/// see `VALID_THEMES` in backend/app/themes.py and `[data-accent]` in
/// web/static/themes.css. A theme on this backend is only an accent colour,
/// not a whole palette, so matching it is just this one value.
enum AccentTheme: String, CaseIterable, Identifiable {
    /// Follows whatever the server reports as its `default_theme`.
    case server
    case amber
    case violet
    case blue
    case emerald
    case rose
    case cyan

    var id: String { rawValue }

    var label: String {
        switch self {
        case .server: return "Match server"
        case .amber: return "Amber"
        case .violet: return "Violet"
        case .blue: return "Blue"
        case .emerald: return "Emerald"
        case .rose: return "Rose"
        case .cyan: return "Cyan"
        }
    }

    /// nil only for `.server`, which has no colour of its own.
    var color: Color? {
        switch self {
        case .server: return nil
        case .amber: return Color(red: 0.976, green: 0.686, blue: 0.176)
        case .violet: return Color(red: 0.545, green: 0.361, blue: 0.965)
        case .blue: return Color(red: 0.231, green: 0.510, blue: 0.965)
        case .emerald: return Color(red: 0.063, green: 0.725, blue: 0.506)
        case .rose: return Color(red: 0.957, green: 0.247, blue: 0.369)
        case .cyan: return Color(red: 0.024, green: 0.714, blue: 0.831)
        }
    }

    /// Maps a server-reported theme id onto an accent, falling back the same
    /// way the backend does when it doesn't recognise one.
    static func fromServer(_ raw: String?) -> AccentTheme {
        guard let raw, let match = AccentTheme(rawValue: raw), match != .server else {
            return .amber
        }
        return match
    }

    static func resolve(_ stored: String, serverTheme: String?) -> Color {
        let chosen = AccentTheme(rawValue: stored) ?? .server
        return chosen.color ?? fromServer(serverTheme).color ?? AccentTheme.amber.color!
    }
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
