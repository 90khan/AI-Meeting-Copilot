import CaptureSourcesObjC
import Darwin
import Foundation

func displaySourcesPayload() -> String? {
    stringFromNativeBuffer(amcp_copy_display_sources_json(), source: "display")
}

func microphoneSourcesPayload() -> String? {
    stringFromNativeBuffer(amcp_copy_microphone_sources_json(), source: "microphone")
}

private func stringFromNativeBuffer(
    _ buffer: UnsafeMutablePointer<CChar>?,
    source: String
) -> String? {
    guard let buffer else {
        return nil
    }
    defer { amcp_free_capture_sources_json(buffer) }
    guard let payload = String(validatingUTF8: buffer) else {
        logEnumerationBridgeClassification(source, classification: "invalid_utf8")
        return nil
    }
    return payload
}

func logEnumerationBridgeClassification(_ source: String, classification: String) {
#if DEBUG
    FileHandle.standardError.write(
        Data("audio-capture \(source) enumeration=\(classification)\n".utf8)
    )
#else
    _ = source
    _ = classification
#endif
}
