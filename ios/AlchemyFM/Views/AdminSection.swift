import SwiftUI

/// Optional operator sign-in, in Settings.
///
/// Kept explicitly optional: listening needs no account, and the only thing
/// this unlocks is hearting the current track — an admin action in the web
/// player too. Someone who never signs in should never notice it's missing.
struct AdminSection: View {
    @Environment(ServerConfig.self) private var server
    @Environment(AdminSession.self) private var admin

    @State private var username = ""
    @State private var password = ""
    @FocusState private var focus: Field?

    private enum Field { case username, password }

    var body: some View {
        Section {
            switch admin.state {
            case .signedIn(let name):
                LabeledContent("Signed in", value: name)
                Button("Sign out", role: .destructive) {
                    admin.signOut(baseURL: server.baseURL)
                    username = ""
                    password = ""
                }

            default:
                TextField("Admin username", text: $username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.username)
                    .focused($focus, equals: .username)
                    .submitLabel(.next)
                    .onSubmit { focus = .password }

                SecureField("Admin password", text: $password)
                    .textContentType(.password)
                    .focused($focus, equals: .password)
                    .submitLabel(.go)
                    .onSubmit(signIn)

                if case .failed(let message) = admin.state {
                    Label(message, systemImage: "exclamationmark.triangle")
                        .font(.footnote)
                        .foregroundStyle(.red)
                }

                Button(action: signIn) {
                    HStack {
                        if case .signingIn = admin.state {
                            ProgressView().controlSize(.small)
                        }
                        Text("Sign in")
                    }
                }
                .disabled(!canSubmit)
            }
        } header: {
            Text("Operator (optional)")
        } footer: {
            Text("Only needed to heart the current track, the same as signing in "
                 + "on the web player. Everything else works signed out. Stored "
                 + "in the Keychain for this server only.")
        }
    }

    private var canSubmit: Bool {
        if case .signingIn = admin.state { return false }
        return !username.trimmingCharacters(in: .whitespaces).isEmpty && !password.isEmpty
    }

    private func signIn() {
        guard canSubmit, let baseURL = server.baseURL else { return }
        focus = nil
        let user = username.trimmingCharacters(in: .whitespaces)
        let secret = password
        Task {
            await admin.signIn(username: user, password: secret, baseURL: baseURL)
            if admin.isSignedIn { password = "" }
        }
    }
}
