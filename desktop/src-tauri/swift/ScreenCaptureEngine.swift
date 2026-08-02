import CaptureSourcesObjC
import Foundation

/// Swift-owned lifecycle facade over the ScreenCaptureKit engine implementation.
/// The native engine owns stream outputs and invokes the callback only while active.
public typealias AMCPAudioFrameCallback = @convention(c) (
    UnsafeMutableRawPointer?, UInt8, UInt32, UInt16, UInt8, Bool, Double,
    UnsafeRawPointer, Int
) -> Void

final class ScreenCaptureEngine {
    private let nativeHandle: UnsafeMutableRawPointer

    init?() {
        guard let nativeHandle = amcp_capture_engine_create() else {
            return nil
        }
        self.nativeHandle = nativeHandle
    }

    deinit {
        _ = amcp_capture_engine_stop(nativeHandle)
        amcp_capture_engine_destroy(nativeHandle)
    }

    func start(
        displayID: UInt32,
        microphoneDeviceID: UnsafePointer<CChar>?,
        includeSystemAudio: Bool,
        includeMicrophone: Bool,
        excludeCurrentProcessAudio: Bool,
        callback: AMCPAudioFrameCallback?,
        context: UnsafeMutableRawPointer?
    ) -> Int32 {
        amcp_capture_engine_start(
            nativeHandle,
            AMCPCaptureConfiguration(
                display_id: displayID,
                microphone_device_id: microphoneDeviceID,
                include_system_audio: includeSystemAudio,
                include_microphone: includeMicrophone,
                exclude_current_process_audio: excludeCurrentProcessAudio
            ),
            callback,
            context
        )
    }

    func stop() -> Int32 {
        amcp_capture_engine_stop(nativeHandle)
    }

    var status: Int32 {
        amcp_capture_engine_status(nativeHandle)
    }
}
