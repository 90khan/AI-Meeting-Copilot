//! Privacy-safe Tauri commands for native audio-capture lifecycle control.

use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tauri::State;
use tokio::sync::Mutex;

use super::{
    authorization::{MicrophoneAuthorizationState, ScreenCaptureAuthorizationState},
    coordinator::{AudioCaptureCoordinator, AudioCaptureCoordinatorError},
    recording_writer::{
        RecordingConfiguration, RecordingFailureCode, RecordingRetentionPolicy,
        RecordingSegmentWriter, RecordingWriter, SidecarRecordingBackendClient,
    },
    sources::{CaptureDisplaySource, CaptureMicrophoneSource},
    status::AudioCaptureStatus,
    swift_bridge::SwiftAudioCaptureBridge,
    types::AudioCaptureConfiguration,
};
use crate::{
    live_transcription::client::{LiveTranscriptionClient, LiveTranscriptionLifecycleStatus},
    sidecar::manager::{LifecycleStatus, SidecarManager},
};

/// One application-owned coordinator. Native bridge calls remain serialized by
/// this mutex; audio callbacks deliver into their separate bounded channel.
pub(crate) struct ManagedAudioCaptureCoordinator {
    coordinator: Mutex<AudioCaptureCoordinator<SwiftAudioCaptureBridge>>,
}

impl ManagedAudioCaptureCoordinator {
    pub(crate) fn new() -> Result<Self, String> {
        let bridge = SwiftAudioCaptureBridge::new().map_err(|_| GENERIC_UNAVAILABLE.to_owned())?;
        Ok(Self {
            coordinator: Mutex::new(AudioCaptureCoordinator::new(bridge)),
        })
    }

    pub(crate) async fn stop(&self) {
        let _ = self.coordinator.lock().await.stop().await;
    }

    /// Application exit is cancellation, not a user-requested transcription
    /// completion. Keep it bounded and do not emit a final live-STT tail.
    pub(crate) async fn abort(&self) {
        self.coordinator.lock().await.abort().await;
    }
}

const GENERIC_UNAVAILABLE: &str = "Audio capture is unavailable.";
const GENERIC_START_FAILURE: &str = "Audio capture could not be started.";
const GENERIC_PERMISSION_FAILURE: &str = "Required capture permission is not granted.";

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CaptureAuthorization {
    screen_capture: ScreenCaptureAuthorizationState,
    microphone: MicrophoneAuthorizationState,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct StartAudioCaptureInput {
    display_id: u32,
    microphone_device_id: Option<String>,
    include_system_audio: bool,
    include_microphone: bool,
    exclude_current_process_audio: bool,
    #[serde(default)]
    recording: RecordingCaptureInput,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RecordingCaptureInput {
    enabled: bool,
    consent_confirmed_at: Option<String>,
    retention_policy: Option<RecordingRetentionPolicy>,
}

#[tauri::command]
pub(crate) async fn get_audio_capture_status(
    capture: State<'_, ManagedAudioCaptureCoordinator>,
) -> Result<AudioCaptureStatus, String> {
    capture
        .coordinator
        .lock()
        .await
        .status()
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())
}

#[tauri::command]
pub(crate) async fn get_capture_authorization(
    capture: State<'_, ManagedAudioCaptureCoordinator>,
) -> Result<CaptureAuthorization, String> {
    let mut coordinator = capture.coordinator.lock().await;
    read_authorization(&mut coordinator)
}

#[tauri::command]
pub(crate) async fn request_capture_authorization(
    capture: State<'_, ManagedAudioCaptureCoordinator>,
) -> Result<CaptureAuthorization, String> {
    let mut coordinator = capture.coordinator.lock().await;
    let screen_capture = coordinator
        .bridge_mut()
        .request_screen_authorization()
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())?;
    let microphone = coordinator
        .bridge_mut()
        .request_microphone_authorization()
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())?;
    Ok(CaptureAuthorization {
        screen_capture,
        microphone,
    })
}

#[tauri::command]
pub(crate) async fn list_capture_displays(
    capture: State<'_, ManagedAudioCaptureCoordinator>,
) -> Result<Vec<CaptureDisplaySource>, String> {
    let result = capture
        .coordinator
        .lock()
        .await
        .bridge_mut()
        .list_displays();
    match result {
        Ok(displays) => {
            #[cfg(debug_assertions)]
            eprintln!("audio-capture display source count={}", displays.len());
            Ok(displays)
        }
        Err(error) => {
            #[cfg(debug_assertions)]
            eprintln!(
                "audio-capture display source enumeration={}",
                error.enumeration_diagnostic()
            );
            Err(GENERIC_UNAVAILABLE.to_owned())
        }
    }
}

#[tauri::command]
pub(crate) async fn list_capture_microphones(
    capture: State<'_, ManagedAudioCaptureCoordinator>,
) -> Result<Vec<CaptureMicrophoneSource>, String> {
    let result = capture
        .coordinator
        .lock()
        .await
        .bridge_mut()
        .list_microphones();
    match result {
        Ok(microphones) => {
            #[cfg(debug_assertions)]
            eprintln!(
                "audio-capture microphone source count={}",
                microphones.len()
            );
            Ok(microphones)
        }
        Err(error) => {
            #[cfg(debug_assertions)]
            eprintln!(
                "audio-capture microphone source enumeration={}",
                error.enumeration_diagnostic()
            );
            Err(GENERIC_UNAVAILABLE.to_owned())
        }
    }
}

#[tauri::command]
pub(crate) async fn start_audio_capture(
    input: StartAudioCaptureInput,
    capture: State<'_, ManagedAudioCaptureCoordinator>,
    sidecar_manager: State<'_, SidecarManager>,
    live_transcription_client: State<'_, LiveTranscriptionClient>,
) -> Result<AudioCaptureStatus, String> {
    validate_connection_preconditions(
        sidecar_manager.status().await.status,
        live_transcription_client.status().await.status,
    )?;

    let configuration = AudioCaptureConfiguration::new(
        input.display_id,
        input.microphone_device_id,
        input.include_system_audio,
        input.include_microphone,
        input.exclude_current_process_audio,
    )
    .map_err(|_| GENERIC_START_FAILURE.to_owned())?;

    {
        let mut coordinator = capture.coordinator.lock().await;
        validate_start_preconditions(&mut coordinator, &configuration)?;
    }

    let (recording_writer, recording_unavailable) = prepare_recording_branch(
        &input.recording,
        sidecar_manager.inner().clone(),
        live_transcription_client.inner().clone(),
    )
    .await;

    let result = {
        let mut coordinator = capture.coordinator.lock().await;
        coordinator
            .start_with_recording(
                &configuration,
                Arc::new(live_transcription_client.inner().clone()),
                recording_writer.clone(),
                recording_unavailable,
            )
            .await
    };
    if result.is_err() {
        if let Some(writer) = recording_writer {
            writer.abort(RecordingFailureCode::CaptureFailed).await;
        }
    }
    result.map_err(map_start_error)
}

async fn prepare_recording_branch(
    input: &RecordingCaptureInput,
    sidecar: SidecarManager,
    live_transcription_client: LiveTranscriptionClient,
) -> (Option<Arc<dyn RecordingSegmentWriter>>, bool) {
    if !input.enabled {
        return (None, false);
    }
    let Some(meeting_id) = live_transcription_client.active_meeting_id().await else {
        return (None, true);
    };
    let writer = Arc::new(RecordingWriter::new(Arc::new(
        SidecarRecordingBackendClient::new(sidecar),
    )));
    let configuration = RecordingConfiguration {
        enabled: true,
        consent_confirmed_at: input.consent_confirmed_at.clone(),
        retention_policy: input
            .retention_policy
            .unwrap_or(RecordingRetentionPolicy::SevenDays),
    };
    match writer.prepare_and_start(meeting_id, configuration).await {
        Ok(true) => (Some(writer), false),
        Ok(false) | Err(_) => (None, true),
    }
}

#[tauri::command]
pub(crate) async fn stop_audio_capture(
    capture: State<'_, ManagedAudioCaptureCoordinator>,
) -> Result<AudioCaptureStatus, String> {
    capture
        .coordinator
        .lock()
        .await
        .stop()
        .await
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())
}

fn read_authorization(
    coordinator: &mut AudioCaptureCoordinator<SwiftAudioCaptureBridge>,
) -> Result<CaptureAuthorization, String> {
    let screen_capture = coordinator
        .bridge_mut()
        .screen_authorization_state()
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())?;
    let microphone = coordinator
        .bridge_mut()
        .microphone_authorization_state()
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())?;
    Ok(CaptureAuthorization {
        screen_capture,
        microphone,
    })
}

fn validate_start_preconditions(
    coordinator: &mut AudioCaptureCoordinator<SwiftAudioCaptureBridge>,
    configuration: &AudioCaptureConfiguration,
) -> Result<(), String> {
    let authorization = read_authorization(coordinator)?;
    if configuration.include_system_audio()
        && authorization.screen_capture != ScreenCaptureAuthorizationState::Authorized
    {
        return Err(GENERIC_PERMISSION_FAILURE.to_owned());
    }
    if configuration.include_microphone()
        && authorization.microphone != MicrophoneAuthorizationState::Authorized
    {
        return Err(GENERIC_PERMISSION_FAILURE.to_owned());
    }

    let display_exists = coordinator
        .bridge_mut()
        .list_displays()
        .map_err(|_| GENERIC_UNAVAILABLE.to_owned())?
        .iter()
        .any(|display| display.id == configuration.display_id());
    if !display_exists {
        return Err(GENERIC_START_FAILURE.to_owned());
    }
    if let Some(microphone_id) = configuration.microphone_device_id() {
        let microphone_exists = coordinator
            .bridge_mut()
            .list_microphones()
            .map_err(|_| GENERIC_UNAVAILABLE.to_owned())?
            .iter()
            .any(|microphone| microphone.id == microphone_id);
        if !microphone_exists {
            return Err(GENERIC_START_FAILURE.to_owned());
        }
    }
    Ok(())
}

fn map_start_error(error: AudioCaptureCoordinatorError) -> String {
    match error {
        AudioCaptureCoordinatorError::AlreadyStarted => {
            "Audio capture is already active.".to_owned()
        }
        _ => GENERIC_START_FAILURE.to_owned(),
    }
}

fn validate_connection_preconditions(
    sidecar_status: LifecycleStatus,
    live_status: LiveTranscriptionLifecycleStatus,
) -> Result<(), String> {
    if sidecar_status != LifecycleStatus::Ready
        || live_status != LiveTranscriptionLifecycleStatus::SessionActive
    {
        return Err(GENERIC_START_FAILURE.to_owned());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{map_start_error, validate_connection_preconditions};
    use crate::{
        audio_capture::coordinator::AudioCaptureCoordinatorError,
        live_transcription::client::LiveTranscriptionLifecycleStatus,
        sidecar::manager::LifecycleStatus,
    };

    #[test]
    fn start_requires_ready_sidecar_and_active_live_session() {
        assert!(validate_connection_preconditions(
            LifecycleStatus::Ready,
            LiveTranscriptionLifecycleStatus::SessionActive,
        )
        .is_ok());
        assert!(validate_connection_preconditions(
            LifecycleStatus::Stopped,
            LiveTranscriptionLifecycleStatus::SessionActive,
        )
        .is_err());
        assert!(validate_connection_preconditions(
            LifecycleStatus::Ready,
            LiveTranscriptionLifecycleStatus::Connected,
        )
        .is_err());
    }

    #[test]
    fn duplicate_start_and_failures_have_privacy_safe_messages() {
        assert_eq!(
            map_start_error(AudioCaptureCoordinatorError::AlreadyStarted),
            "Audio capture is already active."
        );
        assert_eq!(
            map_start_error(AudioCaptureCoordinatorError::SubmissionUnavailable),
            "Audio capture could not be started."
        );
    }
}
