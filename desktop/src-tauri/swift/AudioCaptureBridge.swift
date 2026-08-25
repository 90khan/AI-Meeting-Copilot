import Darwin
import Foundation

/// Main-queue-owned placeholder for a future ScreenCaptureKit audio bridge.
private final class AudioCaptureBridge {
    private var isStarted = false
    private let captureEngine = ScreenCaptureEngine()

    func startPlaceholder() -> Int32 {
        isStarted = true
        return 0
    }

    func stopPlaceholder() -> Int32 {
        isStarted = false
        return 0
    }

    var statusPlaceholder: Int32 {
        isStarted ? 1 : 0
    }

    func startCapture(
        displayID: UInt32,
        microphoneDeviceID: UnsafePointer<CChar>?,
        includeSystemAudio: Bool,
        includeMicrophone: Bool,
        excludeCurrentProcessAudio: Bool,
        callback: AMCPAudioFrameCallback?,
        context: UnsafeMutableRawPointer?
    ) -> Int32 {
        guard let captureEngine else { return 1 }
        let result = captureEngine.start(displayID: displayID, microphoneDeviceID: microphoneDeviceID, includeSystemAudio: includeSystemAudio, includeMicrophone: includeMicrophone, excludeCurrentProcessAudio: excludeCurrentProcessAudio, callback: callback, context: context)
        isStarted = result == 0
        return result
    }

    func stopCapture() -> Int32 {
        guard let captureEngine else { return 1 }
        let result = captureEngine.stop()
        isStarted = false
        return result
    }

    var captureStatus: Int32 { captureEngine?.status ?? 2 }
}

/// Executes all Swift bridge lifecycle work on the main queue.
private func onMainQueue<T>(_ operation: @escaping () -> T) -> T {
    if Thread.isMainThread {
        return operation()
    }

    return DispatchQueue.main.sync(execute: operation)
}

@_cdecl("amcp_audio_capture_bridge_create")
public func amcpAudioCaptureBridgeCreate() -> UnsafeMutableRawPointer? {
    onMainQueue {
        Unmanaged.passRetained(AudioCaptureBridge()).toOpaque()
    }
}

@_cdecl("amcp_audio_capture_bridge_destroy")
public func amcpAudioCaptureBridgeDestroy(_ handle: UnsafeMutableRawPointer?) {
    guard let handle else {
        return
    }

    onMainQueue {
        Unmanaged<AudioCaptureBridge>.fromOpaque(handle).release()
    }
}

@_cdecl("amcp_audio_capture_bridge_start_placeholder")
public func amcpAudioCaptureBridgeStartPlaceholder(_ handle: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else {
        return 1
    }

    return onMainQueue {
        Unmanaged<AudioCaptureBridge>.fromOpaque(handle).takeUnretainedValue().startPlaceholder()
    }
}

@_cdecl("amcp_audio_capture_bridge_stop_placeholder")
public func amcpAudioCaptureBridgeStopPlaceholder(_ handle: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else {
        return 1
    }

    return onMainQueue {
        Unmanaged<AudioCaptureBridge>.fromOpaque(handle).takeUnretainedValue().stopPlaceholder()
    }
}

@_cdecl("amcp_audio_capture_bridge_status_placeholder")
public func amcpAudioCaptureBridgeStatusPlaceholder(_ handle: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else {
        return -1
    }

    return onMainQueue {
        Unmanaged<AudioCaptureBridge>.fromOpaque(handle).takeUnretainedValue().statusPlaceholder
    }
}

private func copyJSONBuffer(
    _ payload: String?,
    source: String
) -> UnsafeMutablePointer<CChar>? {
    guard let payload else {
        return nil
    }
    let buffer = strdup(payload)
    if buffer == nil {
        logEnumerationBridgeClassification(source, classification: "buffer_copy_failed")
    }
    return buffer
}

private func logDisplayBridgeEntry() {
#if DEBUG
    FileHandle.standardError.write(Data("audio-capture bridge display entered\n".utf8))
#endif
}

private func logMicrophoneBridgeEntry() {
#if DEBUG
    FileHandle.standardError.write(Data("audio-capture bridge microphone entered\n".utf8))
#endif
}

@_cdecl("amcp_audio_capture_bridge_free_json_buffer")
public func amcpAudioCaptureBridgeFreeJSONBuffer(_ buffer: UnsafeMutablePointer<CChar>?) {
    guard let buffer else {
        return
    }
    free(buffer)
}

@_cdecl("amcp_audio_capture_bridge_screen_authorization_state")
public func amcpAudioCaptureBridgeScreenAuthorizationState(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(screenAuthorizationPayload(), source: "authorization")
}

@_cdecl("amcp_audio_capture_bridge_request_screen_authorization")
public func amcpAudioCaptureBridgeRequestScreenAuthorization(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(requestScreenAuthorizationPayload(), source: "authorization")
}

@_cdecl("amcp_audio_capture_bridge_microphone_authorization_state")
public func amcpAudioCaptureBridgeMicrophoneAuthorizationState(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(microphoneAuthorizationPayload(), source: "authorization")
}

@_cdecl("amcp_audio_capture_bridge_request_microphone_authorization")
public func amcpAudioCaptureBridgeRequestMicrophoneAuthorization(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(requestMicrophoneAuthorizationPayload(), source: "authorization")
}

@_cdecl("amcp_audio_capture_bridge_list_displays")
public func amcpAudioCaptureBridgeListDisplays(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    logDisplayBridgeEntry()
    return copyJSONBuffer(displaySourcesPayload(), source: "display")
}

@_cdecl("amcp_audio_capture_bridge_list_microphones")
public func amcpAudioCaptureBridgeListMicrophones(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    logMicrophoneBridgeEntry()
    return copyJSONBuffer(microphoneSourcesPayload(), source: "microphone")
}

@_cdecl("amcp_audio_capture_bridge_start_capture")
public func amcpAudioCaptureBridgeStartCapture(_ handle: UnsafeMutableRawPointer?, _ displayID: UInt32, _ microphoneDeviceID: UnsafePointer<CChar>?, _ includeSystemAudio: Bool, _ includeMicrophone: Bool, _ excludeCurrentProcessAudio: Bool, _ callback: AMCPAudioFrameCallback?, _ context: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else { return 1 }
    return onMainQueue { Unmanaged<AudioCaptureBridge>.fromOpaque(handle).takeUnretainedValue().startCapture(displayID: displayID, microphoneDeviceID: microphoneDeviceID, includeSystemAudio: includeSystemAudio, includeMicrophone: includeMicrophone, excludeCurrentProcessAudio: excludeCurrentProcessAudio, callback: callback, context: context) }
}

@_cdecl("amcp_audio_capture_bridge_stop_capture")
public func amcpAudioCaptureBridgeStopCapture(_ handle: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else { return 1 }
    return onMainQueue { Unmanaged<AudioCaptureBridge>.fromOpaque(handle).takeUnretainedValue().stopCapture() }
}

@_cdecl("amcp_audio_capture_bridge_capture_status")
public func amcpAudioCaptureBridgeCaptureStatus(_ handle: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else { return 2 }
    return onMainQueue { Unmanaged<AudioCaptureBridge>.fromOpaque(handle).takeUnretainedValue().captureStatus }
}
