import CaptureSourcesObjC
import Darwin
import Foundation

func displaySourcesPayload() -> String? {
    stringFromNativeBuffer(amcp_copy_display_sources_json())
}

func microphoneSourcesPayload() -> String? {
    stringFromNativeBuffer(amcp_copy_microphone_sources_json())
}

private func stringFromNativeBuffer(_ buffer: UnsafeMutablePointer<CChar>?) -> String? {
    guard let buffer else {
        return nil
    }
    defer { amcp_free_capture_sources_json(buffer) }
    return String(validatingUTF8: buffer)
}
