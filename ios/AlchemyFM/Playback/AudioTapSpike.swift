import AVFoundation
import MediaToolbox

/// SPIKE — throwaway. Answers one question: can we get PCM samples out of the
/// live Icecast stream without touching the playback pipeline?
///
/// MTAudioProcessingTap is the only route to samples from AVPlayer. It's known
/// not to work with HLS; ours is a progressive ICY stream, so it may. This logs
/// whether the process callback ever fires with real audio, and whether
/// attaching the tap disturbs playback.
enum AudioTapSpike {

    private static let process: MTAudioProcessingTapProcessCallback = {
        tap, frames, flags, bufferListInOut, framesOut, flagsOut in

        let status = MTAudioProcessingTapGetSourceAudio(
            tap, frames, bufferListInOut, flagsOut, nil, framesOut
        )
        guard status == noErr else {
            NSLog("[tap] GetSourceAudio failed: %d", Int(status))
            return
        }

        let buffers = UnsafeMutableAudioBufferListPointer(bufferListInOut)
        guard let first = buffers.first,
              let data = first.mData?.assumingMemoryBound(to: Float.self)
        else { return }

        let count = Int(framesOut.pointee)
        guard count > 0 else { return }

        var sum: Float = 0
        var peak: Float = 0
        for i in 0..<count {
            let sample = data[i]
            sum += sample * sample
            peak = max(peak, abs(sample))
        }
        let rms = (sum / Float(count)).squareRoot()

        // Only every ~50th callback, or the log is unreadable.
        counter += 1
        if counter % 50 == 0 {
            NSLog("[tap] frames=%d rms=%.4f peak=%.4f buffers=%d",
                  count, rms, peak, buffers.count)
        }
    }

    nonisolated(unsafe) private static var counter = 0

    /// Attaches the tap to a player item's audio track.
    static func install(on item: AVPlayerItem) {
        Task {
            do {
                let tracks = try await item.asset.loadTracks(withMediaType: .audio)
                guard let track = tracks.first else {
                    NSLog("[tap] NO AUDIO TRACK on the asset — tap impossible")
                    return
                }
                NSLog("[tap] found audio track, attaching")

                var callbacks = MTAudioProcessingTapCallbacks(
                    version: kMTAudioProcessingTapCallbacksVersion_0,
                    clientInfo: nil,
                    init: nil,
                    finalize: nil,
                    prepare: nil,
                    unprepare: nil,
                    process: process
                )

                var tapRef: MTAudioProcessingTap?
                let status = MTAudioProcessingTapCreate(
                    kCFAllocatorDefault,
                    &callbacks,
                    kMTAudioProcessingTapCreationFlag_PostEffects,
                    &tapRef
                )
                guard status == noErr, let tapRef else {
                    NSLog("[tap] MTAudioProcessingTapCreate failed: %d", Int(status))
                    return
                }

                let params = AVMutableAudioMixInputParameters(track: track)
                params.audioTapProcessor = tapRef
                let mix = AVMutableAudioMix()
                mix.inputParameters = [params]
                item.audioMix = mix
                NSLog("[tap] audioMix attached")
            } catch {
                NSLog("[tap] loadTracks failed: %@", error.localizedDescription)
            }
        }
    }
}
