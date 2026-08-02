mod sidecar;

use sidecar::manager::{get_backend_status, start_backend, stop_backend, SidecarManager};
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(SidecarManager::new())
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status
        ])
        .build(tauri::generate_context!())
        .expect("error while running the Tauri application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            let manager = app_handle.state::<SidecarManager>().inner().clone();
            tauri::async_runtime::block_on(manager.stop());
        }
    });
}
