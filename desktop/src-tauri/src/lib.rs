mod live_transcription;
mod sidecar;

use live_transcription::client::{
    connect_live_transcription, disconnect_live_transcription, get_live_transcription_status,
    LiveTranscriptionClient,
};
use sidecar::manager::{get_backend_status, start_backend, stop_backend, SidecarManager};
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_manager = SidecarManager::new();
    let live_transcription_client = LiveTranscriptionClient::new(sidecar_manager.clone());
    let app = tauri::Builder::default()
        .manage(sidecar_manager)
        .manage(live_transcription_client)
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            connect_live_transcription,
            disconnect_live_transcription,
            get_live_transcription_status,
        ])
        .build(tauri::generate_context!())
        .expect("error while running the Tauri application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
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
