import Foundation
import Security

/// Admin credentials, kept in the Keychain.
///
/// Deliberately not `UserDefaults`, where the other preferences live: that is a
/// plist in the app container, readable from a file-system backup. This is an
/// operator's password for their own server.
///
/// Keyed by host, so pointing the app at a different server doesn't silently
/// reuse credentials meant for the previous one.
enum AdminCredentialStore {
    private static let service = "fm.alchemy.AlchemyFM.admin"

    struct Credentials: Codable, Equatable {
        let username: String
        let password: String
    }

    static func save(_ credentials: Credentials, host: String) {
        guard let data = try? JSONEncoder().encode(credentials) else { return }
        delete(host: host)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: host,
            kSecValueData as String: data,
            // Needs to be readable while the app runs in the background to
            // re-authenticate, but never leaves this device.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    static func load(host: String) -> Credentials? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: host,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data
        else { return nil }
        return try? JSONDecoder().decode(Credentials.self, from: data)
    }

    static func delete(host: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: host,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
