use serde::{Deserialize, Serialize};
use std::fmt;

#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordingPlaybackState {
    Idle,
    Loading,
    Ready,
    Starting,
    Streaming,
    Stopping,
    Stopped,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordingPlaybackInfo {
    pub meeting_id: String,
    pub format: String,
    pub capture_anchor_utc: String,
    pub duration_seconds: Option<f64>,
    pub segment_count: usize,
    pub has_gaps: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RecordingPlaybackStatus {
    pub state: RecordingPlaybackState,
    pub info: Option<RecordingPlaybackInfo>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordingPlaybackSeekResult {
    pub at_end: bool,
    /// The opaque playback-stream generation accepted by the manager. It is
    /// present only when a new stream was opened, so the WebView can ignore
    /// events from the preceding stream.
    pub generation: Option<u64>,
    pub segment_index: Option<u32>,
    pub offset_samples: Option<u32>,
    pub resolved_seconds: Option<f64>,
}

/// Plaintext audio is intentionally an internal, non-serializable boundary.
pub(crate) struct PlaybackAudioChunk {
    pub(crate) segment_index: u32,
    pub(crate) bytes: Vec<u8>,
}

impl fmt::Debug for PlaybackAudioChunk {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PlaybackAudioChunk")
            .field("segment_index", &self.segment_index)
            .field("byte_length", &self.bytes.len())
            .finish()
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub(crate) struct BackendPlaybackInfo {
    pub(crate) meeting_id: String,
    pub(crate) format: String,
    pub(crate) capture_anchor_utc: String,
    pub(crate) duration_seconds: Option<f64>,
    pub(crate) segment_count: usize,
    pub(crate) has_gaps: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub(crate) struct BackendPlaybackSeekTarget {
    pub(crate) segment_index: u32,
    pub(crate) offset_samples: u32,
    pub(crate) resolved_seconds: f64,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub(crate) struct BackendPlaybackSeekResolution {
    pub(crate) at_end: bool,
    pub(crate) target: Option<BackendPlaybackSeekTarget>,
}

impl TryFrom<BackendPlaybackSeekResolution> for RecordingPlaybackSeekResult {
    type Error = ();

    fn try_from(value: BackendPlaybackSeekResolution) -> Result<Self, Self::Error> {
        match (value.at_end, value.target) {
            (true, None) => Ok(Self {
                at_end: true,
                generation: None,
                segment_index: None,
                offset_samples: None,
                resolved_seconds: None,
            }),
            (false, Some(target))
                if target.resolved_seconds.is_finite() && target.resolved_seconds >= 0.0 =>
            {
                Ok(Self {
                    at_end: false,
                    generation: None,
                    segment_index: Some(target.segment_index),
                    offset_samples: Some(target.offset_samples),
                    resolved_seconds: Some(target.resolved_seconds),
                })
            }
            _ => Err(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{BackendPlaybackInfo, RecordingPlaybackInfo, RecordingPlaybackState};

    #[test]
    fn backend_playback_info_maps_to_the_safe_public_shape() {
        let meeting_id = "5c4029e9-e29a-4d52-b0e2-e28f94aec2d8";
        let backend = serde_json::from_str::<BackendPlaybackInfo>(&format!(
            r#"{{"meeting_id":"{meeting_id}","format":"wav_pcm16_mono_16khz_segmented_v1","capture_anchor_utc":"2026-01-01T00:00:00Z","duration_seconds":12.5,"segment_count":3,"has_gaps":false}}"#
        ))
        .expect("backend payload is valid");

        let info = RecordingPlaybackInfo::try_from(backend).expect("public info is valid");
        let serialized = serde_json::to_string(&info).expect("public info is serializable");

        assert_eq!(info.meeting_id, meeting_id);
        assert_eq!(info.capture_anchor_utc, "2026-01-01T00:00:00Z");
        assert!(serialized.contains("meetingId"));
        assert!(!serialized.contains("token"));
        assert!(!serialized.contains("http"));
        assert!(!serialized.contains("path"));
    }

    #[test]
    fn malformed_or_unsafe_backend_playback_info_is_rejected() {
        let malformed = serde_json::from_str::<BackendPlaybackInfo>(
            r#"{"meeting_id":"id","format":"wav","capture_anchor_utc":"2026-01-01T00:00:00Z","duration_seconds":1.0,"segment_count":1,"has_gaps":false}"#,
        )
        .expect("JSON structure is parseable");
        assert!(RecordingPlaybackInfo::try_from(malformed).is_err());

        assert!(serde_json::from_str::<BackendPlaybackInfo>(
            r#"{"meeting_id":"id","format":"wav_pcm16_mono_16khz_segmented_v1","capture_anchor_utc":"2026-01-01T00:00:00Z","duration_seconds":1.0,"segment_count":1,"has_gaps":false,"token":"secret"}"#
        )
        .is_err());
    }

    #[test]
    fn missing_or_non_utc_capture_anchor_is_rejected() {
        assert!(serde_json::from_str::<BackendPlaybackInfo>(
            r#"{"meeting_id":"id","format":"wav_pcm16_mono_16khz_segmented_v1","duration_seconds":1.0,"segment_count":1,"has_gaps":false}"#
        )
        .is_err());
        let malformed = serde_json::from_str::<BackendPlaybackInfo>(
            r#"{"meeting_id":"id","format":"wav_pcm16_mono_16khz_segmented_v1","capture_anchor_utc":"2026-01-01T00:00:00+01:00","duration_seconds":1.0,"segment_count":1,"has_gaps":false}"#,
        )
        .expect("JSON structure is parseable");
        assert!(RecordingPlaybackInfo::try_from(malformed).is_err());
    }

    #[test]
    fn public_state_uses_the_expected_serialization() {
        assert_eq!(
            serde_json::to_string(&RecordingPlaybackState::Idle).expect("state serializes"),
            r#""idle""#
        );
        assert_eq!(
            serde_json::to_string(&RecordingPlaybackState::Failed).expect("state serializes"),
            r#""failed""#
        );
    }
}

impl TryFrom<BackendPlaybackInfo> for RecordingPlaybackInfo {
    type Error = ();
    fn try_from(value: BackendPlaybackInfo) -> Result<Self, Self::Error> {
        if value.meeting_id.is_empty()
            || value.format != "wav_pcm16_mono_16khz_segmented_v1"
            || !is_utc_timestamp(&value.capture_anchor_utc)
            || value
                .duration_seconds
                .is_some_and(|v| !v.is_finite() || v < 0.0)
        {
            return Err(());
        }
        Ok(Self {
            meeting_id: value.meeting_id,
            format: value.format,
            capture_anchor_utc: value.capture_anchor_utc,
            duration_seconds: value.duration_seconds,
            segment_count: value.segment_count,
            has_gaps: value.has_gaps,
        })
    }
}

/// Playback timing accepts only the canonical UTC ISO-8601 timestamp emitted
/// by the internal sidecar API. It deliberately rejects local offsets so the
/// WebView cannot accidentally establish a non-UTC synchronization origin.
fn is_utc_timestamp(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 20
        || !value.ends_with('Z')
        || !matches!(bytes.get(4), Some(b'-'))
        || !matches!(bytes.get(7), Some(b'-'))
        || !matches!(bytes.get(10), Some(b'T'))
        || !matches!(bytes.get(13), Some(b':'))
        || !matches!(bytes.get(16), Some(b':'))
    {
        return false;
    }
    let Some(year) = decimal(&bytes[..4]) else {
        return false;
    };
    let Some(month) = decimal(&bytes[5..7]) else {
        return false;
    };
    let Some(day) = decimal(&bytes[8..10]) else {
        return false;
    };
    let Some(hour) = decimal(&bytes[11..13]) else {
        return false;
    };
    let Some(minute) = decimal(&bytes[14..16]) else {
        return false;
    };
    let Some(second) = decimal(&bytes[17..19]) else {
        return false;
    };
    let fraction = &bytes[19..bytes.len() - 1];
    if !fraction.is_empty()
        && (fraction[0] != b'.'
            || fraction.len() == 1
            || !fraction[1..].iter().all(u8::is_ascii_digit))
    {
        return false;
    }
    month >= 1
        && month <= 12
        && day >= 1
        && day <= days_in_month(year, month)
        && hour < 24
        && minute < 60
        && second < 60
}

fn decimal(bytes: &[u8]) -> Option<u32> {
    bytes.iter().try_fold(0_u32, |value, byte| {
        byte.is_ascii_digit()
            .then_some(value * 10 + u32::from(*byte - b'0'))
    })
}

fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if year % 400 == 0 || (year % 4 == 0 && year % 100 != 0) => 29,
        2 => 28,
        _ => 0,
    }
}
