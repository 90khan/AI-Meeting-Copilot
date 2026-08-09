use serde::{Deserialize, Serialize};

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
    pub duration_seconds: Option<f64>,
    pub segment_count: usize,
    pub has_gaps: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RecordingPlaybackStatus {
    pub state: RecordingPlaybackState,
    pub info: Option<RecordingPlaybackInfo>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub(crate) struct BackendPlaybackInfo {
    pub(crate) meeting_id: String,
    pub(crate) format: String,
    pub(crate) duration_seconds: Option<f64>,
    pub(crate) segment_count: usize,
    pub(crate) has_gaps: bool,
}

#[cfg(test)]
mod tests {
    use super::{BackendPlaybackInfo, RecordingPlaybackInfo, RecordingPlaybackState};

    #[test]
    fn backend_playback_info_maps_to_the_safe_public_shape() {
        let meeting_id = "5c4029e9-e29a-4d52-b0e2-e28f94aec2d8";
        let backend = serde_json::from_str::<BackendPlaybackInfo>(&format!(
            r#"{{"meeting_id":"{meeting_id}","format":"m4a","duration_seconds":12.5,"segment_count":3,"has_gaps":false}}"#
        ))
        .expect("backend payload is valid");

        let info = RecordingPlaybackInfo::try_from(backend).expect("public info is valid");
        let serialized = serde_json::to_string(&info).expect("public info is serializable");

        assert_eq!(info.meeting_id, meeting_id);
        assert!(serialized.contains("meetingId"));
        assert!(!serialized.contains("token"));
        assert!(!serialized.contains("http"));
        assert!(!serialized.contains("path"));
    }

    #[test]
    fn malformed_or_unsafe_backend_playback_info_is_rejected() {
        let malformed = serde_json::from_str::<BackendPlaybackInfo>(
            r#"{"meeting_id":"id","format":"wav","duration_seconds":1.0,"segment_count":1,"has_gaps":false}"#,
        )
        .expect("JSON structure is parseable");
        assert!(RecordingPlaybackInfo::try_from(malformed).is_err());

        assert!(serde_json::from_str::<BackendPlaybackInfo>(
            r#"{"meeting_id":"id","format":"m4a","duration_seconds":1.0,"segment_count":1,"has_gaps":false,"token":"secret"}"#
        )
        .is_err());
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
            || value.format != "m4a"
            || value
                .duration_seconds
                .is_some_and(|v| !v.is_finite() || v < 0.0)
        {
            return Err(());
        }
        Ok(Self {
            meeting_id: value.meeting_id,
            format: value.format,
            duration_seconds: value.duration_seconds,
            segment_count: value.segment_count,
            has_gaps: value.has_gaps,
        })
    }
}
