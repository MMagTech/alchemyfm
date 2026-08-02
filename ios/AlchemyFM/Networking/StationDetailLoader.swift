import Foundation
import Observation

/// Polls one station's detail for a page you're *browsing* rather than
/// listening to.
///
/// The player already polls the tuned station hard (750ms) to drive the lock
/// screen. Browsing a different station shouldn't disturb that or start any
/// audio, so this runs its own slower loop and touches nothing else.
@MainActor
@Observable
final class StationDetailLoader {
    private(set) var detail: StationDetail?

    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var loadedSlug: String?

    func start(slug: String, api: AlchemyAPI) {
        guard loadedSlug != slug else { return }
        stop()
        loadedSlug = slug
        detail = nil
        task = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    let detail = try await api.station(slug: slug)
                    guard !Task.isCancelled else { return }
                    self?.detail = detail
                } catch {
                    // Transient — the loop is the retry.
                }
                guard !Task.isCancelled else { return }
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        loadedSlug = nil
    }
}
