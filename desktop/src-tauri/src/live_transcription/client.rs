//! Managed, local-only WebSocket client for one live-transcription session.

#![allow(dead_code)] // Rust-only session and audio APIs are reserved for capture wiring.

use std::{
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use tauri::State;
use thiserror::Error;
use tokio::{
    net::TcpStream,
    sync::Mutex,
    time::{timeout, Duration},
};
use tokio_tungstenite::{
    connect_async, tungstenite::protocol::Message, MaybeTlsStream, WebSocketStream,
};
use uuid::Uuid;

use crate::assist_mode::events::AssistEventSink;
use crate::{assist_mode::events::TranscriptSegmentEvent, sidecar::manager::SidecarManager};

use super::{
    binary_frames::{build_audio_chunk_frame, AudioChunkFrameMetadata, BinaryFrameError},
    protocol::{
        parse_server_message, serialize_end_session, serialize_hello, serialize_start_session,
        AudioSource, ServerMessage,
    },
};

const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
const LIVE_TRANSCRIPTION_PATH: &str = "/api/v1/live-transcription";

type LocalSocket = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LiveTranscriptionLifecycleStatus {
    Disconnected,
    Connecting,
    Connected,
    SessionActive,
    Stopping,
    Failed,
}

/// Public lifecycle status. It deliberately excludes tokens, endpoint details, and protocol data.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LiveTranscriptionStatus {
    pub status: LiveTranscriptionLifecycleStatus,
    pub message: Option<String>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum LiveTranscriptionClientError {
    #[error("The live transcription client is not connected.")]
    NotConnected,
    #[error("The live transcription client is already active.")]
    AlreadyActive,
    #[error("The live transcription session is not active.")]
    SessionNotActive,
    #[error("The audio chunk sequence is invalid.")]
    InvalidSequence,
    #[error("The live transcription connection failed.")]
    ConnectionFailed,
    #[error("The live transcription protocol failed.")]
    ProtocolFailed,
    #[error("The audio chunk timestamp is invalid.")]
    InvalidTimestamp,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ChunkSubmissionResult {
    pub(crate) sequence: u64,
    pub(crate) skipped_silence: bool,
    pub(crate) accepted_segment_count: usize,
    pub(crate) gap_reported: bool,
}

struct ActiveSession {
    session_id: Uuid,
    expected_sequence: u64,
    in_flight: bool,
    anchor_monotonic_seconds: f64,
    anchor_utc_unix_seconds: i64,
    last_capture_started_at_seconds: f64,
}

struct ClientState {
    socket: Option<LocalSocket>,
    status: LiveTranscriptionLifecycleStatus,
    message: Option<String>,
    max_binary_payload_bytes: usize,
    session: Option<ActiveSession>,
}

impl Default for ClientState {
    fn default() -> Self {
        Self {
            socket: None,
            status: LiveTranscriptionLifecycleStatus::Disconnected,
            message: None,
            max_binary_payload_bytes: 0,
            session: None,
        }
    }
}

/// Own one local WebSocket and, at most, one active backend session.
#[derive(Clone)]
pub struct LiveTranscriptionClient {
    sidecar_manager: SidecarManager,
    state: Arc<Mutex<ClientState>>,
    event_sink: Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
}

impl LiveTranscriptionClient {
    pub fn new(sidecar_manager: SidecarManager) -> Self {
        Self {
            sidecar_manager,
            state: Arc::new(Mutex::new(ClientState::default())),
            event_sink: Arc::new(Mutex::new(None)),
        }
    }

    /// Install the application-local event sink after Tauri has an app handle.
    pub fn set_event_sink(&self, event_sink: Arc<dyn AssistEventSink>) {
        let event_sink_state = Arc::clone(&self.event_sink);
        tauri::async_runtime::block_on(async move {
            *event_sink_state.lock().await = Some(event_sink);
        });
    }

    /// Connect and authenticate without exposing the manager-owned token.
    pub async fn connect(&self) -> Result<LiveTranscriptionStatus, LiveTranscriptionClientError> {
        {
            let mut state = self.state.lock().await;
            if !matches!(
                state.status,
                LiveTranscriptionLifecycleStatus::Disconnected
                    | LiveTranscriptionLifecycleStatus::Failed
            ) {
                return Err(LiveTranscriptionClientError::AlreadyActive);
            }
            state.status = LiveTranscriptionLifecycleStatus::Connecting;
            state.message = None;
        }

        let connection = match self.sidecar_manager.live_transcription_connection().await {
            Ok(connection) => connection,
            Err(_) => return Err(self.mark_failed().await),
        };
        if connection.host != "127.0.0.1" || connection.port == 0 {
            return Err(self.mark_failed().await);
        }

        let url = format!(
            "ws://{}:{}{}",
            connection.host, connection.port, LIVE_TRANSCRIPTION_PATH
        );
        let mut socket = match timeout(HANDSHAKE_TIMEOUT, connect_async(url)).await {
            Ok(Ok((socket, _))) => socket,
            _ => return Err(self.mark_failed().await),
        };
        let hello = match serialize_hello(&connection.token, Uuid::new_v4()) {
            Ok(hello) => hello,
            Err(_) => return Err(self.close_then_fail(socket).await),
        };
        if socket.send(Message::Text(hello.into())).await.is_err() {
            return Err(self.close_then_fail(socket).await);
        }

        let ack = match timeout(
            HANDSHAKE_TIMEOUT,
            receive_server_message(&mut socket, &self.event_sink),
        )
        .await
        {
            Ok(Ok(ServerMessage::HelloAck(ack))) => ack,
            _ => return Err(self.close_then_fail(socket).await),
        };

        let mut state = self.state.lock().await;
        state.socket = Some(socket);
        state.status = LiveTranscriptionLifecycleStatus::Connected;
        state.max_binary_payload_bytes = ack.max_binary_payload_bytes;
        state.message = None;
        Ok(public_status(&state))
    }

    /// Close the WebSocket and discard all session state. Repeated calls are safe.
    pub async fn disconnect(&self) -> LiveTranscriptionStatus {
        let socket = {
            let mut state = self.state.lock().await;
            if state.status == LiveTranscriptionLifecycleStatus::Disconnected {
                return public_status(&state);
            }
            state.status = LiveTranscriptionLifecycleStatus::Stopping;
            state.socket.take()
        };
        if let Some(mut socket) = socket {
            let _ = socket.close(None).await;
        }

        let mut state = self.state.lock().await;
        clear_disconnected(&mut state);
        public_status(&state)
    }

    pub async fn status(&self) -> LiveTranscriptionStatus {
        let state = self.state.lock().await;
        public_status(&state)
    }

    /// Begin one validated backend session. This remains Rust-internal until audio capture exists.
    pub(crate) async fn start_session(
        &self,
        meeting_id: Uuid,
        language_hint: Option<&str>,
        source: AudioSource,
    ) -> Result<(), LiveTranscriptionClientError> {
        let request_id = Uuid::new_v4();
        let control = serialize_start_session(request_id, meeting_id, language_hint, source)
            .map_err(|_| LiveTranscriptionClientError::ProtocolFailed)?;
        let mut state = self.state.lock().await;
        if state.status != LiveTranscriptionLifecycleStatus::Connected || state.session.is_some() {
            return Err(LiveTranscriptionClientError::AlreadyActive);
        }
        let socket = state
            .socket
            .as_mut()
            .ok_or(LiveTranscriptionClientError::NotConnected)?;
        if socket.send(Message::Text(control.into())).await.is_err() {
            mark_failed_state(&mut state);
            return Err(LiveTranscriptionClientError::ConnectionFailed);
        }
        match receive_until_session_started(socket, request_id, &self.event_sink).await {
            Ok(started) => {
                state.status = LiveTranscriptionLifecycleStatus::SessionActive;
                state.session = Some(ActiveSession {
                    session_id: started.session_id,
                    expected_sequence: started.expected_sequence,
                    in_flight: false,
                    anchor_monotonic_seconds: 0.0,
                    anchor_utc_unix_seconds: SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .map_err(|_| LiveTranscriptionClientError::InvalidTimestamp)?
                        .as_secs() as i64,
                    last_capture_started_at_seconds: 0.0,
                });
                Ok(())
            }
            Err(error) => {
                mark_failed_state(&mut state);
                Err(error)
            }
        }
    }

    /// Send exactly one finalized WAV chunk and wait for its terminal result.
    pub(crate) async fn send_audio_chunk(
        &self,
        metadata: AudioChunkFrameMetadata,
        wav_payload: &[u8],
    ) -> Result<(), LiveTranscriptionClientError> {
        let mut state = self.state.lock().await;
        validate_chunk_admission(&state, &metadata)?;
        let max_payload = state.max_binary_payload_bytes;
        let session = state
            .session
            .as_mut()
            .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
        let frame = build_audio_chunk_frame(&metadata, wav_payload, max_payload)
            .map_err(map_binary_error)?;
        session.in_flight = true;

        let socket = state
            .socket
            .as_mut()
            .ok_or(LiveTranscriptionClientError::NotConnected)?;
        if socket.send(Message::Binary(frame.into())).await.is_err() {
            mark_failed_state(&mut state);
            return Err(LiveTranscriptionClientError::ConnectionFailed);
        }
        match receive_until_chunk_terminal(socket, metadata.sequence, &self.event_sink).await {
            Ok(()) => {
                if let Some(session) = state.session.as_mut() {
                    session.expected_sequence += 1;
                    session.in_flight = false;
                }
                Ok(())
            }
            Err(error) => {
                if let Some(session) = state.session.as_mut() {
                    session.in_flight = false;
                }
                if error == LiveTranscriptionClientError::ConnectionFailed {
                    mark_failed_state(&mut state);
                }
                Err(error)
            }
        }
    }

    /// Submit one finalized WAV chunk. Session IDs, protocol version, and the
    /// next AMCP sequence remain exclusively owned by this client.
    pub(crate) async fn submit_wav_chunk(
        &self,
        capture_started_at_seconds: f64,
        overlap_seconds: f64,
        wav_payload: Vec<u8>,
    ) -> Result<ChunkSubmissionResult, LiveTranscriptionClientError> {
        if !capture_started_at_seconds.is_finite() || capture_started_at_seconds < 0.0 {
            return Err(LiveTranscriptionClientError::InvalidTimestamp);
        }
        let mut state = self.state.lock().await;
        if state.status != LiveTranscriptionLifecycleStatus::SessionActive {
            return Err(LiveTranscriptionClientError::SessionNotActive);
        }
        let max_payload = state.max_binary_payload_bytes;
        let session = state
            .session
            .as_mut()
            .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
        if session.in_flight {
            return Err(LiveTranscriptionClientError::SessionNotActive);
        }
        if capture_started_at_seconds + f64::EPSILON < session.last_capture_started_at_seconds
            || capture_started_at_seconds < session.anchor_monotonic_seconds
        {
            return Err(LiveTranscriptionClientError::InvalidTimestamp);
        }
        let utc_seconds = session.anchor_utc_unix_seconds
            + (capture_started_at_seconds - session.anchor_monotonic_seconds).floor() as i64;
        let metadata = AudioChunkFrameMetadata {
            session_id: session.session_id,
            sequence: session.expected_sequence,
            capture_started_at: format_utc_timestamp(utc_seconds)?,
            source: AudioSource::Mixed,
            sample_rate_hz: 16_000,
            channels: 1,
            overlap_seconds,
            byte_length: wav_payload.len(),
        };
        let frame = build_audio_chunk_frame(&metadata, &wav_payload, max_payload)
            .map_err(map_binary_error)?;
        session.in_flight = true;
        let socket = state
            .socket
            .as_mut()
            .ok_or(LiveTranscriptionClientError::NotConnected)?;
        if socket.send(Message::Binary(frame.into())).await.is_err() {
            mark_failed_state(&mut state);
            return Err(LiveTranscriptionClientError::ConnectionFailed);
        }
        match receive_until_chunk_result(socket, metadata.sequence, &self.event_sink).await {
            Ok(response) => {
                let session = state
                    .session
                    .as_mut()
                    .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
                session.expected_sequence += 1;
                session.last_capture_started_at_seconds = capture_started_at_seconds;
                session.in_flight = false;
                Ok(ChunkSubmissionResult {
                    sequence: metadata.sequence,
                    skipped_silence: response.skipped_silence,
                    accepted_segment_count: response.accepted_segments.len(),
                    gap_reported: false,
                })
            }
            Err(error) => {
                if let Some(session) = state.session.as_mut() {
                    session.in_flight = false;
                }
                if error == LiveTranscriptionClientError::ConnectionFailed {
                    mark_failed_state(&mut state);
                }
                Err(error)
            }
        }
    }

    /// End the active session using the backend's empty-session last-sequence convention.
    pub(crate) async fn end_session(&self) -> Result<(), LiveTranscriptionClientError> {
        let request_id = Uuid::new_v4();
        let mut state = self.state.lock().await;
        let session = state
            .session
            .as_ref()
            .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
        let control = serialize_end_session(
            request_id,
            session.session_id,
            session.expected_sequence.saturating_sub(1),
        )
        .map_err(|_| LiveTranscriptionClientError::ProtocolFailed)?;
        let session_id = session.session_id;
        let socket = state
            .socket
            .as_mut()
            .ok_or(LiveTranscriptionClientError::NotConnected)?;
        if socket.send(Message::Text(control.into())).await.is_err() {
            mark_failed_state(&mut state);
            return Err(LiveTranscriptionClientError::ConnectionFailed);
        }
        match receive_until_session_stopped(socket, request_id, session_id, &self.event_sink).await
        {
            Ok(()) => {
                state.session = None;
                state.status = LiveTranscriptionLifecycleStatus::Connected;
                Ok(())
            }
            Err(error) => {
                mark_failed_state(&mut state);
                Err(error)
            }
        }
    }

    async fn close_then_fail(&self, mut socket: LocalSocket) -> LiveTranscriptionClientError {
        let _ = socket.close(None).await;
        self.mark_failed().await
    }

    async fn mark_failed(&self) -> LiveTranscriptionClientError {
        let mut state = self.state.lock().await;
        mark_failed_state(&mut state);
        LiveTranscriptionClientError::ConnectionFailed
    }
}

/// Connect to the local backend without ever exposing sidecar secrets to JavaScript.
#[tauri::command]
pub async fn connect_live_transcription(
    client: State<'_, LiveTranscriptionClient>,
) -> Result<LiveTranscriptionStatus, String> {
    client.connect().await.map_err(|error| error.to_string())
}

/// Close the local connection; no session/audio protocol details leave Rust.
#[tauri::command]
pub async fn disconnect_live_transcription(
    client: State<'_, LiveTranscriptionClient>,
) -> Result<LiveTranscriptionStatus, String> {
    Ok(client.disconnect().await)
}

/// Return only the safe, high-level connection lifecycle state.
#[tauri::command]
pub async fn get_live_transcription_status(
    client: State<'_, LiveTranscriptionClient>,
) -> Result<LiveTranscriptionStatus, String> {
    Ok(client.status().await)
}

async fn receive_server_message(
    socket: &mut LocalSocket,
    _event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
) -> Result<ServerMessage, LiveTranscriptionClientError> {
    match socket.next().await {
        Some(Ok(Message::Text(text))) => {
            parse_server_message(&text).map_err(|_| LiveTranscriptionClientError::ProtocolFailed)
        }
        Some(Ok(Message::Close(_))) | None => Err(LiveTranscriptionClientError::ConnectionFailed),
        Some(Ok(_)) => Err(LiveTranscriptionClientError::ProtocolFailed),
        Some(Err(_)) => Err(LiveTranscriptionClientError::ConnectionFailed),
    }
}

async fn receive_until_session_started(
    socket: &mut LocalSocket,
    request_id: Uuid,
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
) -> Result<super::protocol::SessionStarted, LiveTranscriptionClientError> {
    loop {
        match receive_server_message(socket, event_sink).await? {
            ServerMessage::SessionStarted(started) if started.request_id == request_id => {
                return Ok(started)
            }
            ServerMessage::Status => continue,
            message @ (ServerMessage::AssistSegmentUpdate(_)
            | ServerMessage::AssistReplySuggestions(_)) => {
                emit_assist_event(event_sink, message).await;
            }
            ServerMessage::Error(error) if error.fatal => {
                return Err(LiveTranscriptionClientError::ConnectionFailed)
            }
            ServerMessage::Error(_) => return Err(LiveTranscriptionClientError::ProtocolFailed),
            _ => return Err(LiveTranscriptionClientError::ProtocolFailed),
        }
    }
}

async fn receive_until_chunk_terminal(
    socket: &mut LocalSocket,
    sequence: u64,
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
) -> Result<(), LiveTranscriptionClientError> {
    loop {
        match receive_server_message(socket, event_sink).await? {
            ServerMessage::ChunkResult {
                chunk_sequence,
                response,
            } if chunk_sequence == sequence => {
                emit_transcript_events(event_sink, &response).await;
                return Ok(());
            }
            ServerMessage::Status => continue,
            message @ (ServerMessage::AssistSegmentUpdate(_)
            | ServerMessage::AssistReplySuggestions(_)) => {
                emit_assist_event(event_sink, message).await;
            }
            ServerMessage::Error(error) if error.fatal => {
                return Err(LiveTranscriptionClientError::ConnectionFailed)
            }
            ServerMessage::Error(_) => return Err(LiveTranscriptionClientError::ProtocolFailed),
            _ => return Err(LiveTranscriptionClientError::ProtocolFailed),
        }
    }
}

async fn receive_until_chunk_result(
    socket: &mut LocalSocket,
    sequence: u64,
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
) -> Result<super::protocol::ChunkResponse, LiveTranscriptionClientError> {
    loop {
        match receive_server_message(socket, event_sink).await? {
            ServerMessage::ChunkResult {
                chunk_sequence,
                response,
            } if chunk_sequence == sequence => {
                emit_transcript_events(event_sink, &response).await;
                return Ok(response);
            }
            ServerMessage::Status => continue,
            message @ (ServerMessage::AssistSegmentUpdate(_)
            | ServerMessage::AssistReplySuggestions(_)) => {
                emit_assist_event(event_sink, message).await;
            }
            ServerMessage::Error(error) if error.fatal => {
                return Err(LiveTranscriptionClientError::ConnectionFailed)
            }
            ServerMessage::Error(_) => return Err(LiveTranscriptionClientError::ProtocolFailed),
            _ => return Err(LiveTranscriptionClientError::ProtocolFailed),
        }
    }
}

fn format_utc_timestamp(seconds: i64) -> Result<String, LiveTranscriptionClientError> {
    let mut timestamp = seconds as libc::time_t;
    let mut value: libc::tm = unsafe { std::mem::zeroed() };
    if unsafe { libc::gmtime_r(&mut timestamp, &mut value) }.is_null() {
        return Err(LiveTranscriptionClientError::InvalidTimestamp);
    }
    Ok(format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        value.tm_year + 1900,
        value.tm_mon + 1,
        value.tm_mday,
        value.tm_hour,
        value.tm_min,
        value.tm_sec
    ))
}

async fn receive_until_session_stopped(
    socket: &mut LocalSocket,
    request_id: Uuid,
    session_id: Uuid,
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
) -> Result<(), LiveTranscriptionClientError> {
    loop {
        match receive_server_message(socket, event_sink).await? {
            ServerMessage::SessionStopped(stopped)
                if stopped.request_id == request_id && stopped.session_id == session_id =>
            {
                return Ok(())
            }
            ServerMessage::Status => continue,
            message @ (ServerMessage::AssistSegmentUpdate(_)
            | ServerMessage::AssistReplySuggestions(_)) => {
                emit_assist_event(event_sink, message).await;
            }
            ServerMessage::Error(error) if error.fatal => {
                return Err(LiveTranscriptionClientError::ConnectionFailed)
            }
            ServerMessage::Error(_) => return Err(LiveTranscriptionClientError::ProtocolFailed),
            _ => return Err(LiveTranscriptionClientError::ProtocolFailed),
        }
    }
}

async fn emit_assist_event(
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
    message: ServerMessage,
) {
    let sink = event_sink.lock().await.clone();
    let Some(sink) = sink else {
        return;
    };
    match message {
        ServerMessage::AssistSegmentUpdate(event) => sink.emit_segment_update(event),
        ServerMessage::AssistReplySuggestions(event) => sink.emit_reply_suggestions(event),
        _ => {}
    }
}

async fn emit_transcript_events(
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
    response: &super::protocol::ChunkResponse,
) {
    let sink = event_sink.lock().await.clone();
    let Some(sink) = sink else {
        return;
    };
    for segment in &response.accepted_segments {
        sink.emit_transcript_segment(TranscriptSegmentEvent {
            transcript_id: segment.transcript_id,
            text: segment.text.clone(),
            timestamp: segment.timestamp.clone(),
            source: segment.source.as_str().to_owned(),
            speaker: segment.speaker.clone(),
        });
    }
}

fn map_binary_error(error: BinaryFrameError) -> LiveTranscriptionClientError {
    match error {
        BinaryFrameError::Invalid | BinaryFrameError::TooLarge => {
            LiveTranscriptionClientError::ProtocolFailed
        }
    }
}

fn validate_chunk_admission(
    state: &ClientState,
    metadata: &AudioChunkFrameMetadata,
) -> Result<(), LiveTranscriptionClientError> {
    if state.status != LiveTranscriptionLifecycleStatus::SessionActive {
        return Err(LiveTranscriptionClientError::SessionNotActive);
    }
    let session = state
        .session
        .as_ref()
        .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
    if session.in_flight {
        return Err(LiveTranscriptionClientError::AlreadyActive);
    }
    if metadata.session_id != session.session_id || metadata.sequence != session.expected_sequence {
        return Err(LiveTranscriptionClientError::InvalidSequence);
    }
    Ok(())
}

fn mark_failed_state(state: &mut ClientState) {
    state.socket = None;
    state.session = None;
    state.max_binary_payload_bytes = 0;
    state.status = LiveTranscriptionLifecycleStatus::Failed;
    state.message = Some("The live transcription connection failed.".to_owned());
}

fn clear_disconnected(state: &mut ClientState) {
    state.socket = None;
    state.session = None;
    state.max_binary_payload_bytes = 0;
    state.status = LiveTranscriptionLifecycleStatus::Disconnected;
    state.message = None;
}

fn public_status(state: &ClientState) -> LiveTranscriptionStatus {
    LiveTranscriptionStatus {
        status: state.status,
        message: state.message.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        clear_disconnected, mark_failed_state, validate_chunk_admission, ActiveSession,
        ClientState, LiveTranscriptionClientError, LiveTranscriptionLifecycleStatus,
        LiveTranscriptionStatus,
    };
    use crate::live_transcription::{
        binary_frames::AudioChunkFrameMetadata, protocol::AudioSource,
    };
    use uuid::Uuid;

    #[test]
    fn state_cleanup_is_idempotent_and_redacted() {
        let mut state = ClientState::default();
        mark_failed_state(&mut state);
        assert_eq!(state.status, LiveTranscriptionLifecycleStatus::Failed);
        assert_eq!(
            state.message.as_deref(),
            Some("The live transcription connection failed.")
        );

        clear_disconnected(&mut state);
        clear_disconnected(&mut state);
        assert_eq!(state.status, LiveTranscriptionLifecycleStatus::Disconnected);
        assert!(state.message.is_none());
    }

    #[test]
    fn chunk_admission_enforces_one_in_flight_chunk_and_sequence() {
        let session_id = Uuid::nil();
        let mut state = ClientState {
            status: LiveTranscriptionLifecycleStatus::SessionActive,
            max_binary_payload_bytes: 10,
            session: Some(ActiveSession {
                session_id,
                expected_sequence: 2,
                in_flight: false,
                anchor_monotonic_seconds: 0.0,
                anchor_utc_unix_seconds: 0,
                last_capture_started_at_seconds: 0.0,
            }),
            ..ClientState::default()
        };
        let metadata = AudioChunkFrameMetadata {
            session_id,
            sequence: 2,
            capture_started_at: "2026-08-02T10:00:00Z".to_owned(),
            source: AudioSource::Mixed,
            sample_rate_hz: 16_000,
            channels: 1,
            overlap_seconds: 0.5,
            byte_length: 1,
        };

        assert!(validate_chunk_admission(&state, &metadata).is_ok());
        state.session.as_mut().expect("session exists").in_flight = true;
        assert_eq!(
            validate_chunk_admission(&state, &metadata),
            Err(LiveTranscriptionClientError::AlreadyActive)
        );
        state.session.as_mut().expect("session exists").in_flight = false;
        let wrong_sequence = AudioChunkFrameMetadata {
            sequence: 3,
            ..metadata
        };
        assert_eq!(
            validate_chunk_admission(&state, &wrong_sequence),
            Err(LiveTranscriptionClientError::InvalidSequence)
        );
    }

    #[test]
    fn public_status_cannot_serialize_a_token() {
        let status = LiveTranscriptionStatus {
            status: LiveTranscriptionLifecycleStatus::Connected,
            message: None,
        };
        let serialized = serde_json::to_string(&status).expect("status serializes");

        assert!(!serialized.contains("token"));
        assert_eq!(serialized, r#"{"status":"connected","message":null}"#);
    }
}
