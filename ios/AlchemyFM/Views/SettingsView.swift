import SwiftUI

struct SettingsView: View {
    @Environment(ServerConfig.self) private var server
    @Environment(RadioPlayer.self) private var player
    @Environment(\.dismiss) private var dismiss

    @AppStorage(DisplayPreference.showArtistBio) private var showArtistBio = true
    @AppStorage(DisplayPreference.showTrackTrivia) private var showTrackTrivia = true
    @AppStorage(DisplayPreference.appearance) private var appearance = AppearanceMode.system.rawValue
    @AppStorage(DisplayPreference.accent) private var accent = AccentTheme.server.rawValue

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

                AdminSection()

                Section {
                    Picker("Theme", selection: $appearance) {
                        ForEach(AppearanceMode.allCases) { mode in
                            Text(mode.label).tag(mode.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)

                    Picker("Accent", selection: $accent) {
                        ForEach(AccentTheme.allCases) { theme in
                            Label {
                                Text(theme == .server ? serverAccentLabel : theme.label)
                            } icon: {
                                Circle()
                                    .fill(theme.color ?? serverAccentColor)
                                    .frame(width: 14, height: 14)
                            }
                            .tag(theme.rawValue)
                        }
                    }
                } header: {
                    Text("Appearance")
                } footer: {
                    Text("The same accents the web player offers. \"Match server\" "
                         + "follows whatever this server is set to.")
                }

                Section {
                    Toggle("Artist info", isOn: $showArtistBio)
                    Toggle("Track trivia", isOn: $showTrackTrivia)
                } header: {
                    Text("On station pages")
                } footer: {
                    Text("Applies to this device only — the web player keeps "
                         + "showing whatever the server has enabled.")
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
                         + "broadcast, so there's no seeking within a track. On "
                         + "the lock screen, ⏮ and ⏭ change station.")
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

    private var serverAccent: AccentTheme {
        AccentTheme.fromServer(server.serverTheme)
    }

    private var serverAccentLabel: String {
        server.serverTheme == nil ? "Match server" : "Match server (\(serverAccent.label))"
    }

    private var serverAccentColor: Color {
        serverAccent.color ?? .gray
    }

    private static var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "0"
        let build = info?["CFBundleVersion"] as? String ?? "0"
        return "\(short) (\(build))"
    }
}
