//! Privacy-safe capture-source DTOs without display, window, or device names.

use serde::Deserialize;
use thiserror::Error;

use super::swift_bridge::SwiftAudioCaptureBridgeError;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CaptureDisplaySource {
    pub(crate) id: u32,
    pub(crate) width: u32,
    pub(crate) height: u32,
    pub(crate) is_primary: bool,
}

impl CaptureDisplaySource {
    fn validate(self) -> Result<Self, CaptureSourceValidationError> {
        if self.width == 0 || self.height == 0 {
            return Err(CaptureSourceValidationError::InvalidDisplay);
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CaptureMicrophoneSource {
    pub(crate) id: String,
    pub(crate) is_default: bool,
}

impl CaptureMicrophoneSource {
    fn validate(mut self) -> Result<Self, CaptureSourceValidationError> {
        self.id = self.id.trim().to_owned();
        if self.id.is_empty() {
            return Err(CaptureSourceValidationError::InvalidMicrophone);
        }
        Ok(self)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DisplaysPayload {
    displays: Vec<CaptureDisplaySource>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MicrophonesPayload {
    microphones: Vec<CaptureMicrophoneSource>,
}

#[derive(Debug, Error, PartialEq, Eq)]
enum CaptureSourceValidationError {
    #[error("The capture display source is invalid.")]
    InvalidDisplay,
    #[error("The capture microphone source is invalid.")]
    InvalidMicrophone,
}

pub(crate) fn parse_displays(
    payload: &[u8],
) -> Result<Vec<CaptureDisplaySource>, SwiftAudioCaptureBridgeError> {
    let payload = serde_json::from_slice::<DisplaysPayload>(payload)
        .map_err(|_| SwiftAudioCaptureBridgeError::MalformedNativePayload)?;
    payload
        .displays
        .into_iter()
        .map(CaptureDisplaySource::validate)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| SwiftAudioCaptureBridgeError::MalformedNativePayload)
}

pub(crate) fn parse_microphones(
    payload: &[u8],
) -> Result<Vec<CaptureMicrophoneSource>, SwiftAudioCaptureBridgeError> {
    let payload = serde_json::from_slice::<MicrophonesPayload>(payload)
        .map_err(|_| SwiftAudioCaptureBridgeError::MalformedNativePayload)?;
    payload
        .microphones
        .into_iter()
        .map(CaptureMicrophoneSource::validate)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| SwiftAudioCaptureBridgeError::MalformedNativePayload)
}

#[cfg(test)]
mod tests {
    use super::{parse_displays, parse_microphones};
    use crate::audio_capture::swift_bridge::SwiftAudioCaptureBridgeError;

    #[test]
    fn parses_allowlisted_source_payloads_and_empty_lists() {
        let displays = parse_displays(
            br#"{"displays":[{"id":1,"width":1920,"height":1080,"is_primary":true}]}"#,
        )
        .expect("parses displays");
        assert_eq!(displays[0].id, 1);
        assert_eq!(
            parse_microphones(br#"{"microphones":[]}"#).expect("parses"),
            []
        );
    }

    #[test]
    fn rejects_sensitive_or_invalid_source_payloads() {
        assert_eq!(
            parse_displays(br#"{"displays":[{"id":1,"width":0,"height":1080,"is_primary":true}]}"#),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
        assert_eq!(
            parse_microphones(br#"{"microphones":[{"id":" ","is_default":true}]}"#),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
        assert_eq!(
            parse_displays(br#"{"displays":[],"window_title":"private"}"#),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
        assert_eq!(
            parse_microphones(
                br#"{"microphones":[{"id":"device-id","is_default":false,"device_name":"private"}]}"#,
            ),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
    }
}
