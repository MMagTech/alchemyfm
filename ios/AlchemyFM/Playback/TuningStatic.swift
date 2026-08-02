import AVFoundation

/// A short burst of FM-style static, played across a station change.
///
/// Synthesised rather than shipped as an asset: it's band-passed white noise
/// at a randomised centre frequency, so no two switches sound identical and
/// there's no audio file in the bundle.
///
/// Notably this doesn't duck the live stream the way the web player must. There
/// the `<audio>` element keeps playing the old station through the switch, so
/// the volume has to be pulled down and restored — and the restore can be
/// throttled while backgrounded, stranding playback at low volume. Tuning here
/// tears the old AVPlayer down first, so there is already a gap to fill.
@MainActor
final class TuningStatic {
    static let shared = TuningStatic()

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let filter = AVAudioUnitEQ(numberOfBands: 1)
    private var ready = false

    private static let duration = 0.45
    private static let sampleRate = 44_100.0

    private func prepare() -> Bool {
        if ready { return true }

        guard let format = AVAudioFormat(standardFormatWithSampleRate: Self.sampleRate, channels: 1)
        else { return false }

        engine.attach(node)
        engine.attach(filter)

        let band = filter.bands[0]
        band.filterType = .bandPass
        band.bandwidth = 1.0
        band.bypass = false
        filter.globalGain = 0

        engine.connect(node, to: filter, format: format)
        engine.connect(filter, to: engine.mainMixerNode, format: format)

        do {
            try engine.start()
        } catch {
            // A missing sound effect must never take playback down with it.
            return false
        }
        ready = true
        return true
    }

    private func makeBuffer() -> AVAudioPCMBuffer? {
        guard let format = AVAudioFormat(standardFormatWithSampleRate: Self.sampleRate, channels: 1)
        else { return nil }

        let frames = AVAudioFrameCount(Self.sampleRate * Self.duration)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let samples = buffer.floatChannelData?[0]
        else { return nil }
        buffer.frameLength = frames

        let count = Int(frames)
        // Short fades at both ends; a hard edge on noise reads as a click.
        let fade = Int(Self.sampleRate * 0.04)
        for i in 0..<count {
            let noise = Float.random(in: -1...1) * Float.random(in: 0.35...0.60)
            var envelope: Float = 1
            if i < fade {
                envelope = Float(i) / Float(fade)
            } else if i > count - fade {
                envelope = Float(count - i) / Float(fade)
            }
            samples[i] = noise * envelope * 0.22
        }
        return buffer
    }

    /// Plays one burst. Silently does nothing if the engine won't start —
    /// this is decoration, and it should never be the reason a station fails
    /// to tune.
    func play() {
        guard prepare(), let buffer = makeBuffer() else { return }
        filter.bands[0].frequency = Float.random(in: 900...2_700)
        node.scheduleBuffer(buffer, at: nil, options: [])
        node.play()
    }
}
