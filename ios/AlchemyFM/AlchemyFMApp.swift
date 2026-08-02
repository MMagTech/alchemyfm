import SwiftUI

@main
struct AlchemyFMApp: App {
    @State private var server = ServerConfig()
    @State private var player = RadioPlayer()

    @AppStorage(DisplayPreference.appearance) private var appearance = AppearanceMode.system.rawValue

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(server)
                .environment(player)
                // nil follows the system setting.
                .preferredColorScheme(AppearanceMode.resolve(appearance).colorScheme)
        }
    }
}
