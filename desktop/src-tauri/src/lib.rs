mod assist_mode;
mod audio_capture;
mod live_transcription;
mod recording_playback;
mod sidecar;

use audio_capture::commands::{
    get_audio_capture_status, get_capture_authorization, list_capture_displays,
    list_capture_microphones, request_capture_authorization, start_audio_capture,
    stop_audio_capture, ManagedAudioCaptureCoordinator,
};
use live_transcription::client::{
    connect_live_transcription, create_meeting, disconnect_live_transcription,
    end_live_transcription_session, generate_meeting_review, generate_meeting_translation,
    get_live_transcription_status, get_meeting_detail, get_meeting_review, get_meeting_translation,
    list_meetings, start_live_transcription_session, start_meeting, LiveTranscriptionClient,
};
use recording_playback::manager::{
    get_recording_playback_info, get_recording_playback_status, prepare_recording_playback,
    start_recording_playback_stream, stop_recording_playback, RecordingPlaybackManager,
};
use sidecar::manager::{get_backend_status, start_backend, stop_backend, SidecarManager};
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_manager = SidecarManager::new();
    let live_transcription_client = LiveTranscriptionClient::new(sidecar_manager.clone());
    let playback_manager = RecordingPlaybackManager::new(sidecar_manager.clone());
    let audio_capture_coordinator =
        ManagedAudioCaptureCoordinator::new().expect("audio capture bridge is unavailable");
    let app = tauri::Builder::default()
        .manage(sidecar_manager)
        .manage(live_transcription_client.clone())
        .manage(playback_manager)
        .manage(audio_capture_coordinator)
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            connect_live_transcription,
            disconnect_live_transcription,
            get_live_transcription_status,
            end_live_transcription_session,
            create_meeting,
            start_meeting,
            list_meetings,
            get_meeting_detail,
            get_meeting_translation,
            generate_meeting_translation,
            get_meeting_review,
            generate_meeting_review,
            start_live_transcription_session,
            get_audio_capture_status,
            get_capture_authorization,
            request_capture_authorization,
            list_capture_displays,
            list_capture_microphones,
            start_audio_capture,
            stop_audio_capture,
            get_recording_playback_info,
            prepare_recording_playback,
            start_recording_playback_stream,
            stop_recording_playback,
            get_recording_playback_status,
        ])
        .build(tauri::generate_context!())
        .expect("error while running the Tauri application");

    live_transcription_client.set_event_sink(std::sync::Arc::new(
        assist_mode::events::TauriAssistEventSink::new(app.handle().clone()),
    ));

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            let playback = app_handle
                .state::<RecordingPlaybackManager>()
                .inner()
                .clone();
            tauri::async_runtime::block_on(playback.stop());
            let capture = app_handle.state::<ManagedAudioCaptureCoordinator>().inner();
            tauri::async_runtime::block_on(capture.stop());
            let live_transcription_client = app_handle
                .state::<LiveTranscriptionClient>()
                .inner()
                .clone();
            tauri::async_runtime::block_on(live_transcription_client.disconnect());
            let manager = app_handle.state::<SidecarManager>().inner().clone();
            tauri::async_runtime::block_on(manager.stop());
        }
    });
}
