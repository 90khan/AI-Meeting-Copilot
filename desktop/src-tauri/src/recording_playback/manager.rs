use std::sync::Arc;

use reqwest::StatusCode;
use tauri::State;
use tokio::sync::Mutex;
use uuid::Uuid;

use super::types::{
    BackendPlaybackInfo, RecordingPlaybackInfo, RecordingPlaybackState, RecordingPlaybackStatus,
};
use crate::sidecar::manager::SidecarManager;

struct PlaybackState {
    state: RecordingPlaybackState,
    info: Option<RecordingPlaybackInfo>,
    meeting_id: Option<Uuid>,
    /// Reserved for the stream consumer introduced in T9011B-2.
    stream_task: Option<tokio::task::JoinHandle<()>>,
}
impl Default for PlaybackState {
    fn default() -> Self {
        Self {
            state: RecordingPlaybackState::Idle,
            info: None,
            meeting_id: None,
            stream_task: None,
        }
    }
}

#[derive(Clone)]
pub struct RecordingPlaybackManager {
    state: Arc<Mutex<PlaybackState>>,
    sidecar: SidecarManager,
}

impl RecordingPlaybackManager {
    pub fn new(sidecar: SidecarManager) -> Self {
        Self {
            state: Arc::new(Mutex::new(PlaybackState::default())),
            sidecar,
        }
    }
    pub async fn status(&self) -> RecordingPlaybackStatus {
        let state = self.state.lock().await;
        public_status(&state)
    }
    pub async fn load_info(&self, meeting_id: Uuid) -> Result<RecordingPlaybackInfo, ()> {
        {
            let mut state = self.state.lock().await;
            state.state = RecordingPlaybackState::Loading;
        }
        let result = self.fetch_info(meeting_id).await;
        let mut state = self.state.lock().await;
        match result {
            Ok(info) => {
                state.info = Some(info.clone());
                state.meeting_id = Some(meeting_id);
                state.state = RecordingPlaybackState::Ready;
                Ok(info)
            }
            Err(()) => {
                state.state = RecordingPlaybackState::Failed;
                Err(())
            }
        }
    }
    /// Prepare one playback session. Streaming itself is deliberately deferred
    /// to T9011B-2, so a successful preparation returns to `Ready`.
    pub async fn start(&self, meeting_id: Uuid) -> Result<RecordingPlaybackStatus, ()> {
        {
            let state = self.state.lock().await;
            if !matches!(
                state.state,
                RecordingPlaybackState::Idle
                    | RecordingPlaybackState::Stopped
                    | RecordingPlaybackState::Failed
            ) {
                return Err(());
            }
        }
        self.load_info(meeting_id).await?;
        let mut state = self.state.lock().await;
        state.state = RecordingPlaybackState::Starting;
        // There is no stream task in this lifecycle-only milestone.
        state.state = RecordingPlaybackState::Ready;
        Ok(public_status(&state))
    }
    pub async fn stop(&self) -> RecordingPlaybackStatus {
        let mut state = self.state.lock().await;
        state.state = RecordingPlaybackState::Stopping;
        if let Some(task) = state.stream_task.take() {
            task.abort();
        }
        state.info = None;
        state.meeting_id = None;
        state.state = RecordingPlaybackState::Stopped;
        public_status(&state)
    }
    async fn fetch_info(&self, meeting_id: Uuid) -> Result<RecordingPlaybackInfo, ()> {
        let connection = self
            .sidecar
            .live_transcription_connection()
            .await
            .map_err(|_| ())?;
        let response = reqwest::Client::new()
            .get(format!(
                "http://{}:{}/api/v1/internal/recordings/{meeting_id}/playback-info",
                connection.host, connection.port
            ))
            .header("x-ai-meeting-copilot-token", connection.token)
            .send()
            .await
            .map_err(|_| ())?;
        if response.status() != StatusCode::OK {
            return Err(());
        }
        let body = response.text().await.map_err(|_| ())?;
        let info = serde_json::from_str::<BackendPlaybackInfo>(&body).map_err(|_| ())?;
        let info = RecordingPlaybackInfo::try_from(info).map_err(|_| ())?;
        if info.meeting_id != meeting_id.to_string() {
            return Err(());
        }
        Ok(info)
    }
}

#[cfg(test)]
mod tests {
    use super::{PlaybackState, RecordingPlaybackManager};
    use crate::recording_playback::types::{RecordingPlaybackInfo, RecordingPlaybackState};
    use crate::sidecar::manager::SidecarManager;
    use uuid::Uuid;

    #[tokio::test]
    async fn manager_starts_idle_and_stop_is_idempotent() {
        let manager = RecordingPlaybackManager::new(SidecarManager::new());

        assert_eq!(manager.status().await.state, RecordingPlaybackState::Idle);
        assert_eq!(manager.stop().await.state, RecordingPlaybackState::Stopped);
        assert_eq!(manager.stop().await.state, RecordingPlaybackState::Stopped);
        assert!(manager.status().await.info.is_none());
    }

    #[tokio::test]
    async fn unavailable_sidecar_sets_a_privacy_safe_failed_status() {
        let manager = RecordingPlaybackManager::new(SidecarManager::new());

        assert!(manager.start(Uuid::new_v4()).await.is_err());
        let serialized = serde_json::to_string(&manager.status().await).expect("status serializes");

        assert!(serialized.contains("failed"));
        assert!(!serialized.contains("token"));
        assert!(!serialized.contains("http"));
        assert!(!serialized.contains("path"));
    }

    #[tokio::test]
    async fn duplicate_prepare_is_rejected_without_replacing_the_active_session() {
        let manager = RecordingPlaybackManager::new(SidecarManager::new());
        let meeting_id = Uuid::new_v4();
        {
            let mut state = manager.state.lock().await;
            *state = PlaybackState {
                state: RecordingPlaybackState::Ready,
                info: Some(RecordingPlaybackInfo {
                    meeting_id: meeting_id.to_string(),
                    format: "m4a".to_owned(),
                    duration_seconds: Some(1.0),
                    segment_count: 1,
                    has_gaps: false,
                }),
                meeting_id: Some(meeting_id),
                stream_task: None,
            };
        }

        assert!(manager.start(Uuid::new_v4()).await.is_err());
        assert_eq!(manager.status().await.state, RecordingPlaybackState::Ready);
    }
}
fn public_status(state: &PlaybackState) -> RecordingPlaybackStatus {
    RecordingPlaybackStatus {
        state: state.state,
        info: state.info.clone(),
    }
}

#[tauri::command]
pub async fn get_recording_playback_info(
    manager: State<'_, RecordingPlaybackManager>,
    meeting_id: Uuid,
) -> Result<RecordingPlaybackInfo, String> {
    manager
        .load_info(meeting_id)
        .await
        .map_err(|_| "Playback is unavailable.".to_owned())
}
#[tauri::command]
pub async fn prepare_recording_playback(
    manager: State<'_, RecordingPlaybackManager>,
    meeting_id: Uuid,
) -> Result<RecordingPlaybackStatus, String> {
    manager
        .start(meeting_id)
        .await
        .map_err(|_| "Playback is unavailable.".to_owned())
}
#[tauri::command]
pub async fn stop_recording_playback(
    manager: State<'_, RecordingPlaybackManager>,
) -> Result<RecordingPlaybackStatus, String> {
    Ok(manager.stop().await)
}
#[tauri::command]
pub async fn get_recording_playback_status(
    manager: State<'_, RecordingPlaybackManager>,
) -> Result<RecordingPlaybackStatus, String> {
    Ok(manager.status().await)
}
