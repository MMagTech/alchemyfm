import SwiftUI

struct SettingsView: View {
    @Environment(ServerConfig.self) private var server
    @Environment(RadioPlayer.self) private var player
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    ServerURLEditor(initialText: server.displayString) { url in
                        guard url != server.baseURL else {
                            dismiss()
                            return
                        }
                        // Stations, artwork and the stream all live on the old
                        // host — none of it survives the switch.
                        player.leave()
                        server.save(url)
                        dismiss()
                    }
                    .padding(.vertical, 4)
                }

                Section {
                    Button("Forget this server", role: .destructive) {
                        player.leave()
                        server.clear()
                        dismiss()
                    }
                }

                Section("About") {
                    LabeledContent("Version", value: Self.version)
                    Text("Alchemy FM is live radio — everyone hears the same "
                         + "broadcast, so there's no skipping or seeking.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private static var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "0"
        let build = info?["CFBundleVersion"] as? String ?? "0"
        return "\(short) (\(build))"
    }
}
