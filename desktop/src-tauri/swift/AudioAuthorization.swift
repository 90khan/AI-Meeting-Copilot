import AVFoundation
import CoreGraphics
import Foundation

private enum ScreenCaptureAuthorization: String, Encodable {
    case notDetermined = "not_determined"
    case authorized
    case denied
    case restricted
}

private enum MicrophoneAuthorization: String, Encodable {
    case notDetermined = "not_determined"
    case authorized
    case denied
    case restricted
}

private struct AuthorizationPayload<State: Encodable>: Encodable {
    let state: State
}

private let screenAuthorizationRequestKey = "amcp.screenCaptureAuthorizationRequested"

func screenAuthorizationPayload() -> String? {
    let state: ScreenCaptureAuthorization
    if CGPreflightScreenCaptureAccess() {
        state = .authorized
    } else if UserDefaults.standard.bool(forKey: screenAuthorizationRequestKey) {
        // CoreGraphics exposes no richer preflight state. After an explicit
        // request, a failed preflight is safely represented as denied.
        state = .denied
    } else {
        state = .notDetermined
    }
    return encodeAuthorizationPayload(state)
}

func requestScreenAuthorizationPayload() -> String? {
    if CGPreflightScreenCaptureAccess() {
        return encodeAuthorizationPayload(ScreenCaptureAuthorization.authorized)
    }

    guard !UserDefaults.standard.bool(forKey: screenAuthorizationRequestKey) else {
        return encodeAuthorizationPayload(ScreenCaptureAuthorization.denied)
    }

    UserDefaults.standard.set(true, forKey: screenAuthorizationRequestKey)
    let granted = CGRequestScreenCaptureAccess()
    return encodeAuthorizationPayload(
        granted ? ScreenCaptureAuthorization.authorized : ScreenCaptureAuthorization.denied
    )
}

func microphoneAuthorizationPayload() -> String? {
    encodeAuthorizationPayload(currentMicrophoneAuthorization())
}

func requestMicrophoneAuthorizationPayload() -> String? {
    guard currentMicrophoneAuthorization() == .notDetermined else {
        return microphoneAuthorizationPayload()
    }

    // This is an explicit, one-time prompt. The bridge calls it outside the
    // Swift main queue so the system completion handler can run freely.
    let semaphore = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .audio) { _ in
        semaphore.signal()
    }
    semaphore.wait()
    return microphoneAuthorizationPayload()
}

private func currentMicrophoneAuthorization() -> MicrophoneAuthorization {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .notDetermined:
        return .notDetermined
    case .authorized:
        return .authorized
    case .denied:
        return .denied
    case .restricted:
        return .restricted
    @unknown default:
        return .restricted
    }
}

private func encodeAuthorizationPayload<State: Encodable>(_ state: State) -> String? {
    guard let data = try? JSONEncoder().encode(AuthorizationPayload(state: state)) else {
        return nil
    }
    return String(data: data, encoding: .utf8)
}
