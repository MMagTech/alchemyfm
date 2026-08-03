import Foundation
import Observation

/// Optional operator sign-in.
///
/// Everything the app does by default is public and unauthenticated. Signing in
/// unlocks exactly one thing — hearting the current track, which is an admin
/// action in the web player too. Nothing else changes, and the app is fully
/// usable without ever touching this.
@MainActor
@Observable
final class AdminSession {
    enum State: Equatable {
        case signedOut
        case signingIn
        case signedIn(username: String)
        case failed(String)
    }

    private(set) var state: State = .signedOut

    var username: String? {
        if case .signedIn(let name) = state { return name }
        return nil
    }

    var isSignedIn: Bool { username != nil }

    /// Restores a stored sign-in for this host, if there is one.
    ///
    /// The session cookie usually outlives a launch, but re-authenticating is a
    /// single cheap request and avoids the case where it has expired server
    /// side and the heart button silently stops working.
    func restore(for baseURL: URL) async {
        guard let host = baseURL.host(),
              let credentials = AdminCredentialStore.load(host: host)
        else {
            state = .signedOut
            return
        }
        await signIn(
            username: credentials.username,
            password: credentials.password,
            baseURL: baseURL,
            persist: false
        )
    }

    func signIn(username: String, password: String, baseURL: URL, persist: Bool = true) async {
        guard let host = baseURL.host() else { return }
        state = .signingIn
        do {
            let name = try await AlchemyAPI(baseURL: baseURL)
                .adminLogin(username: username, password: password)
            if persist {
                AdminCredentialStore.save(
                    .init(username: username, password: password), host: host
                )
            }
            state = .signedIn(username: name)
        } catch {
            // A stored credential that no longer works shouldn't keep silently
            // retrying on every launch.
            if persist == false { AdminCredentialStore.delete(host: host) }
            state = .failed(error.localizedDescription)
        }
    }

    func signOut(baseURL: URL?) {
        if let host = baseURL?.host() {
            AdminCredentialStore.delete(host: host)
        }
        // Drops the admin_session cookie so the station poll stops returning
        // heart state immediately, rather than until it expires.
        if let baseURL, let storage = HTTPCookieStorage.shared.cookies(for: baseURL) {
            storage.filter { $0.name == "admin_session" }
                .forEach(HTTPCookieStorage.shared.deleteCookie)
        }
        state = .signedOut
    }
}
