import SwiftUI

/// First run. Alchemy FM is self-hosted, so there is no sensible default
/// server — the app is useless until it knows where yours lives.
struct ServerSetupView: View {
    @Environment(ServerConfig.self) private var server

    var body: some View {
        VStack(spacing: 28) {
            Spacer()

            VStack(spacing: 12) {
                Image(systemName: "antenna.radiowaves.left.and.right")
                    .font(.system(size: 52))
                    .foregroundStyle(.tint)
                Text("Alchemy FM")
                    .font(.largeTitle.bold())
                Text("Enter the address of your Alchemy FM server to get started.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

            ServerURLEditor(initialText: "") { url in
                server.save(url)
            }

            Spacer()
            Spacer()
        }
        .padding(.horizontal, 28)
    }
}

/// URL field + a connect button that verifies `GET /api/health` before saving,
/// so a typo surfaces here rather than as an empty station list later.
struct ServerURLEditor: View {
    let initialText: String
    let onSave: (URL) -> Void

    @State private var text: String = ""
    @State private var isChecking = false
    @State private var errorMessage: String?
    @State private var successMessage: String?
    @FocusState private var isFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("http://alchemy.local:8080", text: $text)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .submitLabel(.go)
                .focused($isFocused)
                .onSubmit(connect)

            if let errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.red)
            } else if let successMessage {
                Label(successMessage, systemImage: "checkmark.circle")
                    .font(.footnote)
                    .foregroundStyle(.green)
            } else {
                Text("Include the port if it isn't 80 or 443. http:// is assumed.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Button(action: connect) {
                HStack {
                    if isChecking { ProgressView().controlSize(.small) }
                    Text(isChecking ? "Checking…" : "Connect")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(isChecking || text.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .onAppear { if text.isEmpty { text = initialText } }
    }

    private func connect() {
        guard let url = ServerConfig.normalize(text) else {
            errorMessage = APIError.badURL.errorDescription
            successMessage = nil
            return
        }
        isChecking = true
        errorMessage = nil
        successMessage = nil
        isFocused = false

        Task {
            do {
                let health = try await ServerConfig.validate(url)
                successMessage = "Connected — \(health.stationsEnabled) station"
                    + (health.stationsEnabled == 1 ? "" : "s")
                isChecking = false
                onSave(url)
            } catch {
                errorMessage = error.localizedDescription
                isChecking = false
            }
        }
    }
}
