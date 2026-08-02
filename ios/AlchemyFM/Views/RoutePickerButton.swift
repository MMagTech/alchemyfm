import AVKit
import SwiftUI

/// The system audio-output picker.
///
/// iOS gives apps no way to enumerate or select Bluetooth devices directly —
/// this is the sanctioned route, and it lists Bluetooth speakers, headphones
/// and AirPlay destinations together. Wrapping it here just saves a trip to
/// Control Center.
struct RoutePickerButton: UIViewRepresentable {
    var tint: Color
    var activeTint: Color

    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        // Audio-only app: without this the picker biases towards video
        // destinations and hides some speakers.
        picker.prioritizesVideoDevices = false
        picker.backgroundColor = .clear
        return picker
    }

    func updateUIView(_ picker: AVRoutePickerView, context: Context) {
        picker.tintColor = UIColor(tint)
        picker.activeTintColor = UIColor(activeTint)
    }
}
