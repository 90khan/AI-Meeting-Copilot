//! Lifecycle manager for one local Python backend sidecar process.

use std::{
    collections::VecDeque,
    path::PathBuf,
    process::Stdio,
    sync::Arc,
    time::{Duration, Instant},
};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use reqwest::{Client, StatusCode};
use serde::Serialize;
use tauri::State;
use thiserror::Error;
use tokio::{
    io::{AsyncBufReadExt as _, BufReader},
    process::{Child, Command},
    sync::Mutex,
    time::{sleep, timeout},
};

use super::{readiness::parse_readiness, redaction::redact_diagnostic};

const SIDECAR_HOST: &str = "127.0.0.1";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);
const HEALTH_RETRY_DELAY: Duration = Duration::from_millis(100);
const MAX_READY_LINE_BYTES: usize = 8 * 1024;
const MAX_DIAGNOSTICS: usize = 64;

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleStatus {
    Stopped,
    Starting,
    Ready,
    Failed,
    Stopping,
}

/// Public, serializable sidecar state that intentionally excludes all secrets.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BackendStatus {
    pub status: LifecycleStatus,
    pub host: Option<String>,
    pub port: Option<u16>,
    pub message: Option<String>,
}

#[derive(Debug, Error)]
pub enum SidecarError {
    #[error("The backend sidecar is already running or starting.")]
    AlreadyRunning,
    #[error("The backend sidecar could not be started.")]
    StartupFailed,
    #[error("The backend sidecar is not ready.")]
    NotReady,
}

/// Internal connection data for local Rust components. This deliberately has
/// no public serialization or debug representation because it contains a token.
pub(crate) struct SidecarConnection {
    pub(crate) host: String,
    pub(crate) port: u16,
    pub(crate) token: String,
}

struct ManagerState {
    child: Option<Child>,
    token: Option<String>,
    status: LifecycleStatus,
    host: Option<String>,
    port: Option<u16>,
    message: Option<String>,
    diagnostics: VecDeque<String>,
}

impl Default for ManagerState {
    fn default() -> Self {
        Self {
            child: None,
            token: None,
            status: LifecycleStatus::Stopped,
            host: None,
            port: None,
            message: None,
            diagnostics: VecDeque::new(),
        }
    }
}

/// Own and serialize access to the one backend sidecar for this desktop process.
#[derive(Clone)]
pub struct SidecarManager {
    state: Arc<Mutex<ManagerState>>,
    client: Client,
}

impl Default for SidecarManager {
    fn default() -> Self {
        Self::new()
    }
}

impl SidecarManager {
    pub fn new() -> Self {
        Self {
            state: Arc::new(Mutex::new(ManagerState::default())),
            client: Client::new(),
        }
    }

    /// Start one development sidecar and return only its non-sensitive endpoint state.
    pub async fn start(&self) -> Result<BackendStatus, SidecarError> {
        self.refresh_crash_state().await;

        {
            let mut state = self.state.lock().await;
            if matches!(
                state.status,
                LifecycleStatus::Starting | LifecycleStatus::Ready | LifecycleStatus::Stopping
            ) {
                return Err(SidecarError::AlreadyRunning);
            }
            state.status = LifecycleStatus::Starting;
            state.message = None;
        }

        let token = generate_auth_token();
        let mut child = match development_command(&token).spawn() {
            Ok(child) => child,
            Err(_) => return self.fail_startup(None).await,
        };
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => return self.fail_startup(Some(child)).await,
        };
        let stderr = child.stderr.take();

        {
            let mut state = self.state.lock().await;
            state.token = Some(token.clone());
            state.child = Some(child);
        }

        if let Some(stderr) = stderr {
            self.spawn_stderr_collector(stderr, token.clone());
        }

        let startup = self.readiness_and_health(stdout);
        match timeout(STARTUP_TIMEOUT, startup).await {
            Ok(Ok((host, port))) => {
                let mut state = self.state.lock().await;
                state.status = LifecycleStatus::Ready;
                state.host = Some(host);
                state.port = Some(port);
                state.message = None;
                Ok(public_status(&state))
            }
            _ => self.fail_startup(None).await,
        }
    }

    /// Stop the sidecar, safely reaping it and clearing all sensitive state.
    pub async fn stop(&self) -> BackendStatus {
        let child = {
            let mut state = self.state.lock().await;
            if state.status == LifecycleStatus::Stopped {
                return public_status(&state);
            }
            state.status = LifecycleStatus::Stopping;
            state.child.take()
        };
        if let Some(child) = child {
            terminate_child(child).await;
        }

        let mut state = self.state.lock().await;
        clear_stopped_state(&mut state);
        public_status(&state)
    }

    /// Return public state, detecting a sidecar crash without exposing child output.
    pub async fn status(&self) -> BackendStatus {
        self.refresh_crash_state().await;
        let state = self.state.lock().await;
        public_status(&state)
    }

    /// Return the active loopback connection details for an internal client.
    pub(crate) async fn live_transcription_connection(
        &self,
    ) -> Result<SidecarConnection, SidecarError> {
        self.refresh_crash_state().await;
        let state = self.state.lock().await;
        if state.status != LifecycleStatus::Ready {
            return Err(SidecarError::NotReady);
        }

        match (&state.host, state.port, &state.token) {
            (Some(host), Some(port), Some(token)) if host == SIDECAR_HOST && port > 0 => {
                Ok(SidecarConnection {
                    host: host.clone(),
                    port,
                    token: token.clone(),
                })
            }
            _ => Err(SidecarError::NotReady),
        }
    }

    async fn readiness_and_health(
        &self,
        stdout: tokio::process::ChildStdout,
    ) -> Result<(String, u16), SidecarError> {
        let mut reader = BufReader::new(stdout);
        let line = read_bounded_line(&mut reader).await?;
        if line.is_empty() {
            return Err(SidecarError::StartupFailed);
        }
        let line = std::str::from_utf8(&line).map_err(|_| SidecarError::StartupFailed)?;
        let readiness = parse_readiness(line).map_err(|_| SidecarError::StartupFailed)?;
        self.verify_health(&readiness.host, readiness.port).await?;
        Ok((readiness.host, readiness.port))
    }

    async fn verify_health(&self, host: &str, port: u16) -> Result<(), SidecarError> {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let url = format!("http://{host}:{port}/health");
        while Instant::now() < deadline {
            if let Ok(response) = self.client.get(&url).send().await {
                if response.status() == StatusCode::OK {
                    return Ok(());
                }
            }
            sleep(HEALTH_RETRY_DELAY).await;
        }
        Err(SidecarError::StartupFailed)
    }

    fn spawn_stderr_collector(&self, stderr: tokio::process::ChildStderr, token: String) {
        let state = Arc::clone(&self.state);
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let diagnostic = redact_diagnostic(&line, &token);
                let mut state = state.lock().await;
                if state.diagnostics.len() == MAX_DIAGNOSTICS {
                    state.diagnostics.pop_front();
                }
                state.diagnostics.push_back(diagnostic);
            }
        });
    }

    async fn fail_startup(&self, child: Option<Child>) -> Result<BackendStatus, SidecarError> {
        if let Some(child) = child {
            terminate_child(child).await;
        }
        let child = {
            let mut state = self.state.lock().await;
            state.token = None;
            state.host = None;
            state.port = None;
            state.status = LifecycleStatus::Failed;
            state.message = Some("The backend sidecar could not be started.".to_owned());
            state.child.take()
        };
        if let Some(child) = child {
            terminate_child(child).await;
        }
        Err(SidecarError::StartupFailed)
    }

    async fn refresh_crash_state(&self) {
        let mut state = self.state.lock().await;
        let exited = state
            .child
            .as_mut()
            .and_then(|child| child.try_wait().ok().flatten())
            .is_some();
        if exited
            && matches!(
                state.status,
                LifecycleStatus::Starting | LifecycleStatus::Ready
            )
        {
            state.child = None;
            state.token = None;
            state.host = None;
            state.port = None;
            state.status = LifecycleStatus::Failed;
            state.message = Some("The backend sidecar stopped unexpectedly.".to_owned());
        }
    }
}

/// Tauri command that starts the locally managed backend without exposing its token.
#[tauri::command]
pub async fn start_backend(manager: State<'_, SidecarManager>) -> Result<BackendStatus, String> {
    manager.start().await.map_err(|error| error.to_string())
}

/// Tauri command that idempotently stops the locally managed backend.
#[tauri::command]
pub async fn stop_backend(manager: State<'_, SidecarManager>) -> Result<BackendStatus, String> {
    Ok(manager.stop().await)
}

/// Tauri command that returns the current non-sensitive backend lifecycle state.
#[tauri::command]
pub async fn get_backend_status(
    manager: State<'_, SidecarManager>,
) -> Result<BackendStatus, String> {
    Ok(manager.status().await)
}

fn development_command(token: &str) -> Command {
    let mut command = Command::new("uv");
    command
        .args(["run", "ai-meeting-copilot-sidecar"])
        .current_dir(repository_root())
        .env("AI_MEETING_COPILOT_SIDECAR_AUTH_TOKEN", token)
        .env("AI_MEETING_COPILOT_SIDECAR_HOST", SIDECAR_HOST)
        .env("AI_MEETING_COPILOT_SIDECAR_PORT", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

fn generate_auth_token() -> String {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

async fn read_bounded_line(
    reader: &mut BufReader<tokio::process::ChildStdout>,
) -> Result<Vec<u8>, SidecarError> {
    let mut line = Vec::new();
    loop {
        let available = reader
            .fill_buf()
            .await
            .map_err(|_| SidecarError::StartupFailed)?;
        if available.is_empty() {
            return Ok(line);
        }
        if let Some(newline_index) = available.iter().position(|byte| *byte == b'\n') {
            if line.len() + newline_index > MAX_READY_LINE_BYTES {
                return Err(SidecarError::StartupFailed);
            }
            line.extend_from_slice(&available[..newline_index]);
            reader.consume(newline_index + 1);
            return Ok(line);
        }
        if line.len() + available.len() > MAX_READY_LINE_BYTES {
            return Err(SidecarError::StartupFailed);
        }
        line.extend_from_slice(available);
        let consumed = available.len();
        reader.consume(consumed);
    }
}

async fn terminate_child(mut child: Child) {
    #[cfg(unix)]
    if let Some(process_id) = child.id() {
        // SAFETY: this manager owns the child process and uses its OS-assigned PID.
        unsafe { libc::kill(process_id as i32, libc::SIGTERM) };
    }
    if timeout(SHUTDOWN_TIMEOUT, child.wait()).await.is_err() {
        let _ = child.kill().await;
    }
}

fn clear_stopped_state(state: &mut ManagerState) {
    state.child = None;
    state.token = None;
    state.host = None;
    state.port = None;
    state.message = None;
    state.diagnostics.clear();
    state.status = LifecycleStatus::Stopped;
}

fn public_status(state: &ManagerState) -> BackendStatus {
    BackendStatus {
        status: state.status,
        host: state.host.clone(),
        port: state.port,
        message: state.message.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        generate_auth_token, BackendStatus, LifecycleStatus, SidecarError, SidecarManager,
    };

    #[test]
    fn generated_auth_tokens_are_url_safe_and_large_enough() {
        let token = generate_auth_token();

        assert!(token.len() >= 43);
        assert!(token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_'));
    }

    #[test]
    fn public_status_serialization_excludes_all_secrets() {
        let status = BackendStatus {
            status: LifecycleStatus::Ready,
            host: Some("127.0.0.1".to_owned()),
            port: Some(51842),
            message: None,
        };
        let serialized = serde_json::to_string(&status).expect("status is serializable");

        assert!(!serialized.contains("token"));
        assert_eq!(
            serialized,
            r#"{"status":"ready","host":"127.0.0.1","port":51842,"message":null}"#
        );
    }

    #[tokio::test]
    async fn stopping_an_unstarted_manager_is_idempotent() {
        let manager = SidecarManager::new();

        assert_eq!(manager.stop().await.status, LifecycleStatus::Stopped);
        assert_eq!(manager.stop().await.status, LifecycleStatus::Stopped);
    }

    #[tokio::test]
    async fn duplicate_start_state_is_not_exposed_as_ready() {
        let manager = SidecarManager::new();
        {
            let mut state = manager.state.lock().await;
            state.status = LifecycleStatus::Starting;
        }

        assert!(matches!(
            manager.start().await,
            Err(SidecarError::AlreadyRunning)
        ));
        assert_eq!(manager.status().await.status, LifecycleStatus::Starting);
    }

    #[tokio::test]
    async fn failed_startup_clears_sensitive_state_and_has_a_generic_error() {
        let manager = SidecarManager::new();
        let token = "sensitive-sidecar-token";
        {
            let mut state = manager.state.lock().await;
            state.status = LifecycleStatus::Starting;
            state.token = Some(token.to_owned());
            state.host = Some("127.0.0.1".to_owned());
            state.port = Some(51842);
        }

        let error = manager
            .fail_startup(None)
            .await
            .expect_err("startup must fail");
        let status = manager.status().await;

        assert!(!error.to_string().contains(token));
        assert_eq!(status.status, LifecycleStatus::Failed);
        assert_eq!(status.host, None);
        assert_eq!(status.port, None);
        assert!(!serde_json::to_string(&status)
            .expect("status is serializable")
            .contains(token));
    }
}
