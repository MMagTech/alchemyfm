import SwiftUI

struct RootView: View {
    @Environment(ServerConfig.self) private var server
    @Environment(AdminSession.self) private var admin

    var body: some View {
        Group {
            if server.isConfigured {
                StationListView()
            } else {
                ServerSetupView()
            }
        }
        // Credentials are stored per host, so switching servers re-evaluates
        // from scratch rather than carrying a sign-in across.
        .task(id: server.baseURL) {
            guard let baseURL = server.baseURL else { return }
            await admin.restore(for: baseURL)
        }
    }
}

/// Shared cover-art view. Every image the API points at is either an absolute
/// URL (station artwork) or a same-origin path (`/api/cover/…`), so resolution
/// always goes through the API client.
struct Artwork: View {
    let path: String?
    let api: AlchemyAPI?
    var cornerRadius: CGFloat = 10

    var body: some View {
        AsyncImage(url: api?.resolve(path)) { phase in
            switch phase {
            case .success(let image):
                image.resizable().aspectRatio(contentMode: .fill)
            default:
                ZStack {
                    Rectangle().fill(.quaternary)
                    Image(systemName: "antenna.radiowaves.left.and.right")
                        .font(.title3)
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }
}

/// Small pulsing dot used for the on-air badge.
struct OnAirBadge: View {
    let isOnAir: Bool
    let listeners: Int

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(isOnAir ? Color.green : Color.secondary)
                .frame(width: 6, height: 6)
            if isOnAir {
                Text(listeners == 1 ? "1 listener" : "\(listeners) listeners")
            } else {
                Text("Off air")
            }
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }
}
