import Darwin
import Foundation

/// Main-queue-owned placeholder for a future ScreenCaptureKit audio bridge.
private final class AudioCaptureBridge {
    private var isStarted = false

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

private func copyJSONBuffer(_ payload: String?) -> UnsafeMutablePointer<CChar>? {
    guard let payload else {
        return nil
    }
    return strdup(payload)
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
    return copyJSONBuffer(screenAuthorizationPayload())
}

@_cdecl("amcp_audio_capture_bridge_request_screen_authorization")
public func amcpAudioCaptureBridgeRequestScreenAuthorization(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(requestScreenAuthorizationPayload())
}

@_cdecl("amcp_audio_capture_bridge_microphone_authorization_state")
public func amcpAudioCaptureBridgeMicrophoneAuthorizationState(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(microphoneAuthorizationPayload())
}

@_cdecl("amcp_audio_capture_bridge_request_microphone_authorization")
public func amcpAudioCaptureBridgeRequestMicrophoneAuthorization(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(requestMicrophoneAuthorizationPayload())
}

@_cdecl("amcp_audio_capture_bridge_list_displays")
public func amcpAudioCaptureBridgeListDisplays(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(displaySourcesPayload())
}

@_cdecl("amcp_audio_capture_bridge_list_microphones")
public func amcpAudioCaptureBridgeListMicrophones(
    _ handle: UnsafeMutableRawPointer?
) -> UnsafeMutablePointer<CChar>? {
    guard handle != nil else {
        return nil
    }
    return copyJSONBuffer(microphoneSourcesPayload())
}
