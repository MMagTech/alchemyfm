import Foundation
import Observation

/// Favourite stations, stored on the device.
///
/// Deliberately local. The API already sorts `featured` stations first, but
/// that is the operator's choice for every listener; this is one person's, on
/// one phone, and the two shouldn't overwrite each other.
@MainActor
@Observable
final class FavoritesStore {
    private static let key = "alchemyfm.favorites"

    private(set) var slugs: Set<String> = []

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        slugs = Set(defaults.stringArray(forKey: Self.key) ?? [])
    }

    func isFavorite(_ slug: String) -> Bool {
        slugs.contains(slug)
    }

    func toggle(_ slug: String) {
        if slugs.contains(slug) {
            slugs.remove(slug)
        } else {
            slugs.insert(slug)
        }
        defaults.set(Array(slugs), forKey: Self.key)
    }

    /// Favourites first, each group keeping the server's ordering — so the
    /// operator's `featured`/`sort_order` still decides what leads within each
    /// group rather than being replaced by insertion order.
    func partition(_ stations: [StationSummary]) -> (favorites: [StationSummary], others: [StationSummary]) {
        var favorites: [StationSummary] = []
        var others: [StationSummary] = []
        for station in stations {
            if slugs.contains(station.slug) {
                favorites.append(station)
            } else {
                others.append(station)
            }
        }
        return (favorites, others)
    }
}
