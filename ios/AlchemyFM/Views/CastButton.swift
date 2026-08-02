import GoogleCast
import SwiftUI

/// Google's own cast button, which owns the device-picker sheet and its
/// connected/connecting states. Rebuilding it would mean reimplementing
/// discovery UI for no gain.
struct CastButton: UIViewRepresentable {
    var tint: Color

    func makeUIView(context: Context) -> GCKUICastButton {
        let button = GCKUICastButton()
        button.triggersDefaultCastDialog = true
        return button
    }

    func updateUIView(_ button: GCKUICastButton, context: Context) {
        button.tintColor = UIColor(tint)
    }
}
