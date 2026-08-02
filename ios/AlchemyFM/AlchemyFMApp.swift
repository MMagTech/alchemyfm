import SwiftUI

@main
struct AlchemyFMApp: App {
    @State private var server = ServerConfig()
    @State private var player = RadioPlayer()
    @State private var favorites = FavoritesStore()
    @State private var admin = AdminSession()

    @AppStorage(DisplayPreference.appearance) private var appearance = AppearanceMode.system.rawValue

    init() {
        // Before any Cast UI exists, since GCKUICastButton needs the shared
        // context to have been configured.
        CastController.shared.start()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(server)
                .environment(player)
                .environment(favorites)
                .environment(admin)
                // nil follows the system setting.
                .preferredColorScheme(AppearanceMode.resolve(appearance).colorScheme)
        }
    }
}
