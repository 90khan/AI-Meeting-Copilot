//! Privacy-safe authorization states returned by the native bridge.

use serde::Deserialize;

use super::swift_bridge::SwiftAudioCaptureBridgeError;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ScreenCaptureAuthorizationState {
    NotDetermined,
    Authorized,
    Denied,
    Restricted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum MicrophoneAuthorizationState {
    NotDetermined,
    Authorized,
    Denied,
    Restricted,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthorizationPayload<State> {
    state: State,
}

pub(crate) fn parse_screen_authorization(
    payload: &[u8],
) -> Result<ScreenCaptureAuthorizationState, SwiftAudioCaptureBridgeError> {
    serde_json::from_slice::<AuthorizationPayload<ScreenCaptureAuthorizationState>>(payload)
        .map(|payload| payload.state)
        .map_err(|_| SwiftAudioCaptureBridgeError::MalformedNativePayload)
}

pub(crate) fn parse_microphone_authorization(
    payload: &[u8],
) -> Result<MicrophoneAuthorizationState, SwiftAudioCaptureBridgeError> {
    serde_json::from_slice::<AuthorizationPayload<MicrophoneAuthorizationState>>(payload)
        .map(|payload| payload.state)
        .map_err(|_| SwiftAudioCaptureBridgeError::MalformedNativePayload)
}

#[cfg(test)]
mod tests {
    use super::{
        parse_microphone_authorization, parse_screen_authorization, MicrophoneAuthorizationState,
        ScreenCaptureAuthorizationState,
    };
    use crate::audio_capture::swift_bridge::SwiftAudioCaptureBridgeError;

    #[test]
    fn parses_allowlisted_authorization_states() {
        assert_eq!(
            parse_screen_authorization(br#"{"state":"authorized"}"#).expect("parses"),
            ScreenCaptureAuthorizationState::Authorized
        );
        assert_eq!(
            parse_microphone_authorization(br#"{"state":"restricted"}"#).expect("parses"),
            MicrophoneAuthorizationState::Restricted
        );
    }

    #[test]
    fn rejects_unknown_or_extended_authorization_payloads() {
        assert_eq!(
            parse_screen_authorization(br#"{"state":"unknown"}"#),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
        assert_eq!(
            parse_microphone_authorization(br#"{"state":"denied","device":"private"}"#),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
    }
}
