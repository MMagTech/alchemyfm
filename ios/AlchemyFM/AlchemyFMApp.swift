import SwiftUI

@main
struct AlchemyFMApp: App {
    @State private var server = ServerConfig()
    @State private var player = RadioPlayer()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(server)
                .environment(player)
                .preferredColorScheme(.dark)
        }
    }
}
