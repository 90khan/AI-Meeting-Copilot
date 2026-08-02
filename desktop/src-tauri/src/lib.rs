mod assist_mode;
mod audio_capture;
mod live_transcription;
mod sidecar;

use audio_capture::commands::{
    get_audio_capture_status, get_capture_authorization, list_capture_displays,
    list_capture_microphones, request_capture_authorization, start_audio_capture,
    stop_audio_capture, ManagedAudioCaptureCoordinator,
};
use live_transcription::client::{
    connect_live_transcription, disconnect_live_transcription, get_live_transcription_status,
    start_live_transcription_session, LiveTranscriptionClient,
};
use sidecar::manager::{get_backend_status, start_backend, stop_backend, SidecarManager};
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_manager = SidecarManager::new();
    let live_transcription_client = LiveTranscriptionClient::new(sidecar_manager.clone());
    let audio_capture_coordinator =
        ManagedAudioCaptureCoordinator::new().expect("audio capture bridge is unavailable");
    let app = tauri::Builder::default()
        .manage(sidecar_manager)
        .manage(live_transcription_client.clone())
        .manage(audio_capture_coordinator)
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            connect_live_transcription,
            disconnect_live_transcription,
            get_live_transcription_status,
            start_live_transcription_session,
            get_audio_capture_status,
            get_capture_authorization,
            request_capture_authorization,
            list_capture_displays,
            list_capture_microphones,
            start_audio_capture,
            stop_audio_capture,
        ])
        .build(tauri::generate_context!())
        .expect("error while running the Tauri application");

    live_transcription_client.set_event_sink(std::sync::Arc::new(
        assist_mode::events::TauriAssistEventSink::new(app.handle().clone()),
    ));

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
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
