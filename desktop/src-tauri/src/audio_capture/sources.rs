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
    let value = parse_top_level_value(payload, "displays")?;
    let payload = serde_json::from_value::<DisplaysPayload>(value.clone())
        .map_err(|_| classify_display_dto_failure(&value))?;
    payload
        .displays
        .into_iter()
        .map(CaptureDisplaySource::validate)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| classify_display_dto_failure(&value))
}

pub(crate) fn parse_microphones(
    payload: &[u8],
) -> Result<Vec<CaptureMicrophoneSource>, SwiftAudioCaptureBridgeError> {
    let value = parse_top_level_value(payload, "microphones")?;
    let payload = serde_json::from_value::<MicrophonesPayload>(value.clone())
        .map_err(|_| classify_microphone_dto_failure(&value))?;
    payload
        .microphones
        .into_iter()
        .map(CaptureMicrophoneSource::validate)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| classify_microphone_dto_failure(&value))
}

fn parse_top_level_value(
    payload: &[u8],
    expected_collection_name: &str,
) -> Result<serde_json::Value, SwiftAudioCaptureBridgeError> {
    let value = serde_json::from_slice::<serde_json::Value>(payload)
        .map_err(|_| SwiftAudioCaptureBridgeError::JsonParseFailed)?;
    let Some(object) = value.as_object() else {
        return Err(SwiftAudioCaptureBridgeError::TopLevelShapeMismatch);
    };
    if object.len() != 1
        || !object.contains_key(expected_collection_name)
        || !object[expected_collection_name].is_array()
    {
        return Err(SwiftAudioCaptureBridgeError::TopLevelShapeMismatch);
    }
    Ok(value)
}

fn classify_display_dto_failure(value: &serde_json::Value) -> SwiftAudioCaptureBridgeError {
    let Some(items) = value["displays"].as_array() else {
        return SwiftAudioCaptureBridgeError::DisplayFieldSetMismatch;
    };
    for item in items {
        let Some(object) = item.as_object() else {
            return SwiftAudioCaptureBridgeError::DisplayFieldSetMismatch;
        };
        if object.len() != 4
            || !object.contains_key("id")
            || !object.contains_key("width")
            || !object.contains_key("height")
            || !object.contains_key("is_primary")
        {
            return SwiftAudioCaptureBridgeError::DisplayFieldSetMismatch;
        }
        match checked_u32(&object["id"]) {
            Err(NumberFailure::Type) => return SwiftAudioCaptureBridgeError::DisplayIdTypeMismatch,
            Err(NumberFailure::OutOfRange) => {
                return SwiftAudioCaptureBridgeError::DisplayIdOutOfRange
            }
            Ok(_) => {}
        }
        match checked_u32(&object["width"]) {
            Err(NumberFailure::Type) => {
                return SwiftAudioCaptureBridgeError::DisplayWidthTypeMismatch
            }
            Err(NumberFailure::OutOfRange) => {
                return SwiftAudioCaptureBridgeError::DisplayWidthOutOfRange
            }
            Ok(0) => return SwiftAudioCaptureBridgeError::DisplayWidthZero,
            Ok(_) => {}
        }
        match checked_u32(&object["height"]) {
            Err(NumberFailure::Type) => {
                return SwiftAudioCaptureBridgeError::DisplayHeightTypeMismatch
            }
            Err(NumberFailure::OutOfRange) => {
                return SwiftAudioCaptureBridgeError::DisplayHeightOutOfRange
            }
            Ok(0) => return SwiftAudioCaptureBridgeError::DisplayHeightZero,
            Ok(_) => {}
        }
        if !object["is_primary"].is_boolean() {
            return SwiftAudioCaptureBridgeError::DisplayIsPrimaryTypeMismatch;
        }
    }
    SwiftAudioCaptureBridgeError::DtoValidationFailed
}

fn classify_microphone_dto_failure(value: &serde_json::Value) -> SwiftAudioCaptureBridgeError {
    let Some(items) = value["microphones"].as_array() else {
        return SwiftAudioCaptureBridgeError::MicrophoneFieldSetMismatch;
    };
    for item in items {
        let Some(object) = item.as_object() else {
            return SwiftAudioCaptureBridgeError::MicrophoneFieldSetMismatch;
        };
        if object.len() != 2 || !object.contains_key("id") || !object.contains_key("is_default") {
            return SwiftAudioCaptureBridgeError::MicrophoneFieldSetMismatch;
        }
        let Some(id) = object["id"].as_str() else {
            return SwiftAudioCaptureBridgeError::MicrophoneIdTypeMismatch;
        };
        if id.trim().is_empty() {
            return SwiftAudioCaptureBridgeError::MicrophoneIdBlank;
        }
        if !object["is_default"].is_boolean() {
            return SwiftAudioCaptureBridgeError::MicrophoneIsDefaultTypeMismatch;
        }
    }
    SwiftAudioCaptureBridgeError::DtoValidationFailed
}

enum NumberFailure {
    Type,
    OutOfRange,
}

fn checked_u32(value: &serde_json::Value) -> Result<u32, NumberFailure> {
    let Some(value) = value.as_u64() else {
        return Err(NumberFailure::Type);
    };
    u32::try_from(value).map_err(|_| NumberFailure::OutOfRange)
}

#[cfg(test)]
mod tests {
    use super::{parse_displays, parse_microphones};
    use crate::audio_capture::swift_bridge::SwiftAudioCaptureBridgeError;

    #[test]
    fn accepts_native_boolean_flags_and_preserves_platform_identifier_contracts() {
        let displays = parse_displays(
            br#"{"displays":[{"id":402653184,"width":1728,"height":1117,"is_primary":true}]}"#,
        )
        .expect("numeric display ID and boolean flag parse");
        assert_eq!(displays[0].id, 402653184);
        assert!(displays[0].is_primary);

        let microphones = parse_microphones(
            br#"{"microphones":[{"id":"opaque-device:synthetic-0001","is_default":true}]}"#,
        )
        .expect("opaque microphone ID and boolean flag parse");
        assert_eq!(microphones[0].id, "opaque-device:synthetic-0001");
        assert!(microphones[0].is_default);
    }

    #[test]
    fn rejects_numeric_boolean_flags_and_invalid_sources() {
        assert_eq!(
            parse_displays(br#"{"displays":[{"id":1,"width":1920,"height":1080,"is_primary":1}]}"#,),
            Err(SwiftAudioCaptureBridgeError::DisplayIsPrimaryTypeMismatch)
        );
        assert_eq!(
            parse_microphones(br#"{"microphones":[{"id":"opaque-device","is_default":0}]}"#),
            Err(SwiftAudioCaptureBridgeError::MicrophoneIsDefaultTypeMismatch)
        );
        assert_eq!(
            parse_displays(br#"{"displays":[{"id":1,"width":0,"height":1080,"is_primary":true}]}"#),
            Err(SwiftAudioCaptureBridgeError::DisplayWidthZero)
        );
        assert_eq!(
            parse_microphones(br#"{"microphones":[{"id":" ","is_default":true}]}"#),
            Err(SwiftAudioCaptureBridgeError::MicrophoneIdBlank)
        );
    }

    #[test]
    fn rejects_malformed_or_extended_native_payloads() {
        assert_eq!(
            parse_displays(br#"not-json"#),
            Err(SwiftAudioCaptureBridgeError::JsonParseFailed)
        );
        assert_eq!(
            parse_displays(br#"[]"#),
            Err(SwiftAudioCaptureBridgeError::TopLevelShapeMismatch)
        );
        assert_eq!(
            parse_microphones(br#"{"microphones":"not-an-array"}"#),
            Err(SwiftAudioCaptureBridgeError::TopLevelShapeMismatch)
        );
        assert_eq!(
            parse_microphones(
                br#"{"microphones":[{"id":"device-id","is_default":false,"device_name":"private"}]}"#,
            ),
            Err(SwiftAudioCaptureBridgeError::MicrophoneFieldSetMismatch)
        );
    }

    #[test]
    fn classifies_temporary_dto_failures_without_retaining_payload_data() {
        assert_eq!(
            parse_displays(br#"{"displays":[{"id":1,"width":1920,"height":1080,"is_primary":1}]}"#,),
            Err(SwiftAudioCaptureBridgeError::DisplayIsPrimaryTypeMismatch)
        );
        assert_eq!(
            parse_microphones(br#"{"microphones":[{"id":1,"is_default":true}]}"#),
            Err(SwiftAudioCaptureBridgeError::MicrophoneIdTypeMismatch)
        );
        assert_eq!(
            parse_displays(br#"{"displays":[{"id":1,"width":0,"height":1080,"is_primary":true}]}"#,),
            Err(SwiftAudioCaptureBridgeError::DisplayWidthZero)
        );
        assert_eq!(
            parse_microphones(br#"{"microphones":[{"id":" ","is_default":true}]}"#),
            Err(SwiftAudioCaptureBridgeError::MicrophoneIdBlank)
        );
    }
}
