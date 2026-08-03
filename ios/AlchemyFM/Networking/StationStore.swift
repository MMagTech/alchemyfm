import Foundation
import Observation

/// Backs the station grid. Refreshes on the same 15s cadence as the web home
/// page so listener counts and on-air badges don't go stale while the app sits
/// open.
@MainActor
@Observable
final class StationStore {
    private(set) var stations: [StationSummary] = []
    private(set) var errorMessage: String?
    private(set) var hasLoaded = false

    @ObservationIgnored private var refreshTask: Task<Void, Never>?

    func start(api: AlchemyAPI) {
        guard refreshTask == nil else { return }
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh(api: api)
                guard !Task.isCancelled else { return }
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    func stop() {
        refreshTask?.cancel()
        refreshTask = nil
    }

    func refresh(api: AlchemyAPI) async {
        do {
            stations = try await api.stations()
            errorMessage = nil
        } catch {
            // Keep whatever we already showed — a dropped poll shouldn't blank
            // the grid out from under someone who is listening.
            errorMessage = stations.isEmpty ? error.localizedDescription : nil
        }
        hasLoaded = true
    }

    /// Called when the server URL changes, so stale stations don't linger.
    func reset() {
        stop()
        stations = []
        errorMessage = nil
        hasLoaded = false
    }
}
