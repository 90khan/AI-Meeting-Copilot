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
