//! Managed, local-only WebSocket client for one live-transcription session.

#![allow(dead_code)] // Rust-only session and audio APIs are reserved for capture wiring.

use std::{
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use futures_util::{SinkExt, StreamExt};
use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use tauri::State;
use thiserror::Error;
use tokio::{
    net::TcpStream,
    sync::{mpsc, oneshot, Mutex},
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
        AssistModeConfiguration, AudioSource, ServerMessage,
    },
};

const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
const LIVE_TRANSCRIPTION_PATH: &str = "/api/v1/live-transcription";

type LocalSocket = WebSocketStream<MaybeTlsStream<TcpStream>>;
type LocalWriter = futures_util::stream::SplitSink<LocalSocket, Message>;
type LocalReader = futures_util::stream::SplitStream<LocalSocket>;
type InboundMessage = Result<ServerMessage, LiveTranscriptionClientError>;

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

#[derive(Debug, Clone, Copy, Error, PartialEq, Eq)]
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
    assist_mode: AssistModeConfiguration,
}

struct PendingStart {
    request_id: Uuid,
    responder:
        oneshot::Sender<Result<super::protocol::SessionStarted, LiveTranscriptionClientError>>,
}

struct PendingChunk {
    sequence: u64,
    responder:
        oneshot::Sender<Result<super::protocol::ChunkResponse, LiveTranscriptionClientError>>,
}

struct PendingEnd {
    request_id: Uuid,
    session_id: Uuid,
    responder: oneshot::Sender<Result<(), LiveTranscriptionClientError>>,
}

struct ClientState {
    writer: Option<Arc<Mutex<LocalWriter>>>,
    reader_task: Option<tokio::task::JoinHandle<()>>,
    dispatcher_task: Option<tokio::task::JoinHandle<()>>,
    pending_start: Option<PendingStart>,
    pending_chunk: Option<PendingChunk>,
    pending_end: Option<PendingEnd>,
    status: LiveTranscriptionLifecycleStatus,
    message: Option<String>,
    max_binary_payload_bytes: usize,
    session: Option<ActiveSession>,
}

impl Default for ClientState {
    fn default() -> Self {
        Self {
            writer: None,
            reader_task: None,
            dispatcher_task: None,
            pending_start: None,
            pending_chunk: None,
            pending_end: None,
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
    operation_lock: Arc<Mutex<()>>,
}

impl LiveTranscriptionClient {
    pub fn new(sidecar_manager: SidecarManager) -> Self {
        Self {
            sidecar_manager,
            state: Arc::new(Mutex::new(ClientState::default())),
            event_sink: Arc::new(Mutex::new(None)),
            operation_lock: Arc::new(Mutex::new(())),
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

        let ack = match timeout(HANDSHAKE_TIMEOUT, receive_handshake(&mut socket)).await {
            Ok(Ok(ServerMessage::HelloAck(ack))) => ack,
            _ => return Err(self.close_then_fail(socket).await),
        };

        let (writer, reader) = socket.split();
        let writer = Arc::new(Mutex::new(writer));
        let (inbound_sender, inbound_receiver) = mpsc::channel(16);
        let reader_task = tokio::spawn(run_reader(reader, inbound_sender));
        let dispatcher_task = tokio::spawn(run_dispatcher(
            inbound_receiver,
            Arc::clone(&self.state),
            Arc::clone(&self.event_sink),
        ));

        let mut state = self.state.lock().await;
        state.writer = Some(writer);
        state.reader_task = Some(reader_task);
        state.dispatcher_task = Some(dispatcher_task);
        state.status = LiveTranscriptionLifecycleStatus::Connected;
        state.max_binary_payload_bytes = ack.max_binary_payload_bytes;
        state.message = None;
        Ok(public_status(&state))
    }

    /// Close the WebSocket and discard all session state. Repeated calls are safe.
    pub async fn disconnect(&self) -> LiveTranscriptionStatus {
        let (writer, reader_task, dispatcher_task) = {
            let mut state = self.state.lock().await;
            if state.status == LiveTranscriptionLifecycleStatus::Disconnected {
                return public_status(&state);
            }
            state.status = LiveTranscriptionLifecycleStatus::Stopping;
            (
                state.writer.take(),
                state.reader_task.take(),
                state.dispatcher_task.take(),
            )
        };
        if let Some(writer) = writer {
            let _ = writer.lock().await.send(Message::Close(None)).await;
        }
        if let Some(task) = reader_task {
            task.abort();
            let _ = task.await;
        }
        if let Some(task) = dispatcher_task {
            task.abort();
            let _ = task.await;
        }

        let mut state = self.state.lock().await;
        fail_pending_operations(&mut state);
        clear_disconnected(&mut state);
        public_status(&state)
    }

    pub async fn status(&self) -> LiveTranscriptionStatus {
        let state = self.state.lock().await;
        public_status(&state)
    }

    async fn create_meeting(
        &self,
        name: String,
    ) -> Result<MeetingIdentifier, LiveTranscriptionClientError> {
        if name.trim().is_empty() {
            return Err(LiveTranscriptionClientError::ProtocolFailed);
        }
        let connection = self
            .sidecar_manager
            .live_transcription_connection()
            .await
            .map_err(|_| LiveTranscriptionClientError::NotConnected)?;
        let response = reqwest::Client::new()
            .post(format!(
                "http://{}:{}/api/v1/meetings",
                connection.host, connection.port
            ))
            .header("content-type", "application/json")
            .body(serde_json::json!({ "name": name }).to_string())
            .send()
            .await
            .map_err(|_| LiveTranscriptionClientError::ConnectionFailed)?;
        if response.status() != StatusCode::CREATED {
            return Err(LiveTranscriptionClientError::ProtocolFailed);
        }
        let payload = response
            .text()
            .await
            .map_err(|_| LiveTranscriptionClientError::ProtocolFailed)?;
        serde_json::from_str::<MeetingIdentifier>(&payload)
            .map_err(|_| LiveTranscriptionClientError::ProtocolFailed)
    }

    async fn start_meeting(&self, meeting_id: Uuid) -> Result<(), LiveTranscriptionClientError> {
        let connection = self
            .sidecar_manager
            .live_transcription_connection()
            .await
            .map_err(|_| LiveTranscriptionClientError::NotConnected)?;
        let response = reqwest::Client::new()
            .post(format!(
                "http://{}:{}/api/v1/meetings/{meeting_id}/start",
                connection.host, connection.port
            ))
            .send()
            .await
            .map_err(|_| LiveTranscriptionClientError::ConnectionFailed)?;
        if response.status() == StatusCode::NO_CONTENT {
            Ok(())
        } else {
            Err(LiveTranscriptionClientError::ProtocolFailed)
        }
    }

    /// Begin one validated backend session. This remains Rust-internal until audio capture exists.
    pub(crate) async fn start_session(
        &self,
        meeting_id: Uuid,
        language_hint: Option<&str>,
        source: AudioSource,
        assist_mode: AssistModeConfiguration,
    ) -> Result<(), LiveTranscriptionClientError> {
        let _operation = self.operation_lock.lock().await;
        let request_id = Uuid::new_v4();
        let control =
            serialize_start_session(request_id, meeting_id, language_hint, source, &assist_mode)
                .map_err(|_| LiveTranscriptionClientError::ProtocolFailed)?;
        let (writer, receiver) = {
            let mut state = self.state.lock().await;
            if state.status != LiveTranscriptionLifecycleStatus::Connected
                || state.session.is_some()
            {
                return Err(LiveTranscriptionClientError::AlreadyActive);
            }
            if state.pending_start.is_some() {
                return Err(LiveTranscriptionClientError::AlreadyActive);
            }
            let writer = state
                .writer
                .as_ref()
                .cloned()
                .ok_or(LiveTranscriptionClientError::NotConnected)?;
            let (sender, receiver) = oneshot::channel();
            state.pending_start = Some(PendingStart {
                request_id,
                responder: sender,
            });
            (writer, receiver)
        };
        if writer
            .lock()
            .await
            .send(Message::Text(control.into()))
            .await
            .is_err()
        {
            self.clear_pending_start(request_id).await;
            return Err(self.mark_failed().await);
        }
        match wait_for_response(receiver).await {
            Ok(started) => {
                let mut state = self.state.lock().await;
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
                    assist_mode,
                });
                Ok(())
            }
            Err(error) => {
                self.clear_pending_start(request_id).await;
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
        self.send_chunk_and_wait(metadata.clone(), wav_payload)
            .await?;
        self.complete_chunk(metadata.sequence, None).await
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
        let state = self.state.lock().await;
        if state.status != LiveTranscriptionLifecycleStatus::SessionActive {
            return Err(LiveTranscriptionClientError::SessionNotActive);
        }
        let session = state
            .session
            .as_ref()
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
        drop(state);
        match self
            .send_chunk_and_wait(metadata.clone(), &wav_payload)
            .await
        {
            Ok(response) => {
                self.complete_chunk(metadata.sequence, Some(capture_started_at_seconds))
                    .await?;
                Ok(ChunkSubmissionResult {
                    sequence: metadata.sequence,
                    skipped_silence: response.skipped_silence,
                    accepted_segment_count: response.accepted_segments.len(),
                    gap_reported: false,
                })
            }
            Err(error) => Err(error),
        }
    }

    /// End the active session using the backend's empty-session last-sequence convention.
    pub(crate) async fn end_session(&self) -> Result<(), LiveTranscriptionClientError> {
        let _operation = self.operation_lock.lock().await;
        let request_id = Uuid::new_v4();
        let (writer, receiver, control, session_id) = {
            let mut state = self.state.lock().await;
            let session = state
                .session
                .as_ref()
                .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
            if state.pending_end.is_some() {
                return Err(LiveTranscriptionClientError::AlreadyActive);
            }
            let session_id = session.session_id;
            let control = serialize_end_session(
                request_id,
                session_id,
                session.expected_sequence.saturating_sub(1),
            )
            .map_err(|_| LiveTranscriptionClientError::ProtocolFailed)?;
            let writer = state
                .writer
                .as_ref()
                .cloned()
                .ok_or(LiveTranscriptionClientError::NotConnected)?;
            let (sender, receiver) = oneshot::channel();
            state.pending_end = Some(PendingEnd {
                request_id,
                session_id,
                responder: sender,
            });
            (writer, receiver, control, session_id)
        };
        if writer
            .lock()
            .await
            .send(Message::Text(control.into()))
            .await
            .is_err()
        {
            self.clear_pending_end(request_id, session_id).await;
            return Err(self.mark_failed().await);
        }
        match wait_for_response(receiver).await {
            Ok(()) => {
                let mut state = self.state.lock().await;
                state.session = None;
                state.status = LiveTranscriptionLifecycleStatus::Connected;
                Ok(())
            }
            Err(error) => {
                self.clear_pending_end(request_id, session_id).await;
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
        fail_pending_operations(&mut state);
        mark_failed_state(&mut state);
        LiveTranscriptionClientError::ConnectionFailed
    }

    async fn clear_pending_start(&self, request_id: Uuid) {
        let mut state = self.state.lock().await;
        if state
            .pending_start
            .as_ref()
            .is_some_and(|pending| pending.request_id == request_id)
        {
            state.pending_start = None;
        }
    }

    async fn clear_pending_end(&self, request_id: Uuid, session_id: Uuid) {
        let mut state = self.state.lock().await;
        if state.pending_end.as_ref().is_some_and(|pending| {
            pending.request_id == request_id && pending.session_id == session_id
        }) {
            state.pending_end = None;
        }
    }

    async fn send_chunk_and_wait(
        &self,
        metadata: AudioChunkFrameMetadata,
        wav_payload: &[u8],
    ) -> Result<super::protocol::ChunkResponse, LiveTranscriptionClientError> {
        let _operation = self.operation_lock.lock().await;
        let (writer, receiver, frame) = {
            let mut state = self.state.lock().await;
            validate_chunk_admission(&state, &metadata)?;
            if state.pending_chunk.is_some() {
                return Err(LiveTranscriptionClientError::AlreadyActive);
            }
            let frame =
                build_audio_chunk_frame(&metadata, wav_payload, state.max_binary_payload_bytes)
                    .map_err(map_binary_error)?;
            let writer = state
                .writer
                .as_ref()
                .cloned()
                .ok_or(LiveTranscriptionClientError::NotConnected)?;
            let (sender, receiver) = oneshot::channel();
            state.pending_chunk = Some(PendingChunk {
                sequence: metadata.sequence,
                responder: sender,
            });
            state
                .session
                .as_mut()
                .ok_or(LiveTranscriptionClientError::SessionNotActive)?
                .in_flight = true;
            (writer, receiver, frame)
        };

        if writer
            .lock()
            .await
            .send(Message::Binary(frame.into()))
            .await
            .is_err()
        {
            self.clear_pending_chunk(metadata.sequence).await;
            return Err(self.mark_failed().await);
        }
        match wait_for_response(receiver).await {
            Ok(response) => Ok(response),
            Err(error) => {
                self.clear_pending_chunk(metadata.sequence).await;
                Err(error)
            }
        }
    }

    async fn clear_pending_chunk(&self, sequence: u64) {
        let mut state = self.state.lock().await;
        if state
            .pending_chunk
            .as_ref()
            .is_some_and(|pending| pending.sequence == sequence)
        {
            state.pending_chunk = None;
        }
        if let Some(session) = state.session.as_mut() {
            if session.expected_sequence == sequence {
                session.in_flight = false;
            }
        }
    }

    async fn complete_chunk(
        &self,
        sequence: u64,
        capture_started_at_seconds: Option<f64>,
    ) -> Result<(), LiveTranscriptionClientError> {
        let mut state = self.state.lock().await;
        let session = state
            .session
            .as_mut()
            .ok_or(LiveTranscriptionClientError::SessionNotActive)?;
        if session.expected_sequence != sequence {
            return Err(LiveTranscriptionClientError::InvalidSequence);
        }
        session.expected_sequence += 1;
        if let Some(timestamp) = capture_started_at_seconds {
            session.last_capture_started_at_seconds = timestamp;
        }
        session.in_flight = false;
        Ok(())
    }
}

/// Public, secret-free input for a one-session configuration snapshot.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartLiveTranscriptionSessionInput {
    meeting_id: Uuid,
    language_hint: Option<String>,
    source: AudioSource,
    assist_mode: AssistModeConfiguration,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateMeetingInput {
    name: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MeetingIdentifier {
    meeting_id: Uuid,
}

/// Start a session with configuration fixed for its entire lifecycle.
#[tauri::command]
pub async fn start_live_transcription_session(
    client: State<'_, LiveTranscriptionClient>,
    input: StartLiveTranscriptionSessionInput,
) -> Result<LiveTranscriptionStatus, String> {
    client
        .start_session(
            input.meeting_id,
            input.language_hint.as_deref(),
            input.source,
            input.assist_mode,
        )
        .await
        .map_err(|_| "The live transcription session could not be started.".to_owned())?;
    Ok(client.status().await)
}

#[tauri::command]
pub async fn create_meeting(
    client: State<'_, LiveTranscriptionClient>,
    input: CreateMeetingInput,
) -> Result<MeetingIdentifier, String> {
    client
        .create_meeting(input.name)
        .await
        .map_err(|_| "The Meeting could not be created.".to_owned())
}

#[tauri::command]
pub async fn start_meeting(
    client: State<'_, LiveTranscriptionClient>,
    meeting_id: Uuid,
) -> Result<(), String> {
    client
        .start_meeting(meeting_id)
        .await
        .map_err(|_| "The Meeting could not be started.".to_owned())
}

#[tauri::command]
pub async fn end_live_transcription_session(
    client: State<'_, LiveTranscriptionClient>,
) -> Result<LiveTranscriptionStatus, String> {
    client
        .end_session()
        .await
        .map_err(|_| "The live transcription session could not be ended.".to_owned())?;
    Ok(client.status().await)
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

async fn receive_handshake(
    socket: &mut LocalSocket,
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

async fn run_reader(mut reader: LocalReader, inbound: mpsc::Sender<InboundMessage>) {
    loop {
        let parsed = match reader.next().await {
            Some(Ok(Message::Text(text))) => parse_server_message(&text)
                .map_err(|_| LiveTranscriptionClientError::ProtocolFailed),
            Some(Ok(Message::Close(_))) | None => {
                Err(LiveTranscriptionClientError::ConnectionFailed)
            }
            Some(Ok(_)) => Err(LiveTranscriptionClientError::ProtocolFailed),
            Some(Err(_)) => Err(LiveTranscriptionClientError::ConnectionFailed),
        };
        let terminal = parsed.is_err();
        if inbound.send(parsed).await.is_err() || terminal {
            return;
        }
    }
}

async fn run_dispatcher(
    mut inbound: mpsc::Receiver<InboundMessage>,
    state: Arc<Mutex<ClientState>>,
    event_sink: Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
) {
    while let Some(message) = inbound.recv().await {
        if dispatch_message(&state, &event_sink, message).await {
            return;
        }
    }
    let mut state = state.lock().await;
    fail_pending_operations(&mut state);
    mark_failed_state(&mut state);
}

/// Returns true when the dispatcher must stop after a terminal transport failure.
async fn dispatch_message(
    state: &Arc<Mutex<ClientState>>,
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
    message: InboundMessage,
) -> bool {
    match message {
        Err(_) => {
            let mut state = state.lock().await;
            fail_pending_operations(&mut state);
            mark_failed_state(&mut state);
            true
        }
        Ok(ServerMessage::SessionStarted(started)) => {
            let responder = {
                let mut state = state.lock().await;
                match state.pending_start.take() {
                    Some(pending) if pending.request_id == started.request_id => {
                        Some(pending.responder)
                    }
                    pending => {
                        state.pending_start = pending;
                        None
                    }
                }
            };
            if let Some(responder) = responder {
                let _ = responder.send(Ok(started));
            }
            false
        }
        Ok(ServerMessage::ChunkResult {
            chunk_sequence,
            response,
        }) => {
            let responder = {
                let mut state = state.lock().await;
                match state.pending_chunk.take() {
                    Some(pending) if pending.sequence == chunk_sequence => Some(pending.responder),
                    pending => {
                        state.pending_chunk = pending;
                        None
                    }
                }
            };
            if let Some(responder) = responder {
                // A missing sink is a deliberate no-op during non-Tauri tests. A real
                // delivery failure is fatal: losing finalized transcript events would
                // desynchronize the desktop from persisted backend state.
                if emit_transcript_events(event_sink, &response).await.is_err() {
                    let mut state = state.lock().await;
                    let _ = responder.send(Err(LiveTranscriptionClientError::ConnectionFailed));
                    fail_pending_operations(&mut state);
                    mark_failed_state(&mut state);
                    return true;
                }
                let _ = responder.send(Ok(response));
            }
            false
        }
        Ok(ServerMessage::SessionStopped(stopped)) => {
            let responder = {
                let mut state = state.lock().await;
                match state.pending_end.take() {
                    Some(pending)
                        if pending.request_id == stopped.request_id
                            && pending.session_id == stopped.session_id =>
                    {
                        Some(pending.responder)
                    }
                    pending => {
                        state.pending_end = pending;
                        None
                    }
                }
            };
            if let Some(responder) = responder {
                let _ = responder.send(Ok(()));
            }
            false
        }
        Ok(ServerMessage::Error(error)) => {
            let mut state = state.lock().await;
            fail_pending_operations_with(
                &mut state,
                if error.fatal {
                    LiveTranscriptionClientError::ConnectionFailed
                } else {
                    LiveTranscriptionClientError::ProtocolFailed
                },
            );
            if error.fatal {
                mark_failed_state(&mut state);
                true
            } else {
                false
            }
        }
        Ok(
            message @ (ServerMessage::AssistSegmentUpdate(_)
            | ServerMessage::AssistReplySuggestions(_)),
        ) => {
            if !event_routing_enabled(state).await
                || emit_assist_event(event_sink, message).await.is_err()
            {
                if event_routing_enabled(state).await {
                    let mut state = state.lock().await;
                    fail_pending_operations(&mut state);
                    mark_failed_state(&mut state);
                    return true;
                }
            }
            false
        }
        Ok(ServerMessage::Status) => false,
        Ok(_) => {
            let mut state = state.lock().await;
            fail_pending_operations_with(&mut state, LiveTranscriptionClientError::ProtocolFailed);
            false
        }
    }
}

async fn wait_for_response<T>(
    receiver: oneshot::Receiver<Result<T, LiveTranscriptionClientError>>,
) -> Result<T, LiveTranscriptionClientError> {
    match timeout(HANDSHAKE_TIMEOUT, receiver).await {
        Ok(Ok(result)) => result,
        Ok(Err(_)) => Err(LiveTranscriptionClientError::ConnectionFailed),
        Err(_) => Err(LiveTranscriptionClientError::ConnectionFailed),
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

async fn event_routing_enabled(state: &Arc<Mutex<ClientState>>) -> bool {
    matches!(
        state.lock().await.status,
        LiveTranscriptionLifecycleStatus::Connected
            | LiveTranscriptionLifecycleStatus::SessionActive
    )
}

async fn emit_assist_event(
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
    message: ServerMessage,
) -> Result<(), ()> {
    let sink = event_sink.lock().await.clone();
    let Some(sink) = sink else {
        return Ok(());
    };
    match message {
        ServerMessage::AssistSegmentUpdate(event) => {
            sink.emit_segment_update(event).map_err(|_| ())
        }
        ServerMessage::AssistReplySuggestions(event) => {
            sink.emit_reply_suggestions(event).map_err(|_| ())
        }
        _ => Ok(()),
    }
}

async fn emit_transcript_events(
    event_sink: &Arc<Mutex<Option<Arc<dyn AssistEventSink>>>>,
    response: &super::protocol::ChunkResponse,
) -> Result<(), ()> {
    let sink = event_sink.lock().await.clone();
    let Some(sink) = sink else {
        return Ok(());
    };
    for segment in &response.accepted_segments {
        sink.emit_transcript_segment(TranscriptSegmentEvent {
            transcript_id: segment.transcript_id,
            text: segment.text.clone(),
            timestamp: segment.timestamp.clone(),
            source: segment.source.as_str().to_owned(),
            speaker: segment.speaker.clone(),
        })
        .map_err(|_| ())?;
    }
    Ok(())
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
    state.writer = None;
    state.session = None;
    state.max_binary_payload_bytes = 0;
    state.status = LiveTranscriptionLifecycleStatus::Failed;
    state.message = Some("The live transcription connection failed.".to_owned());
}

fn clear_disconnected(state: &mut ClientState) {
    state.writer = None;
    state.session = None;
    state.max_binary_payload_bytes = 0;
    state.status = LiveTranscriptionLifecycleStatus::Disconnected;
    state.message = None;
}

fn fail_pending_operations(state: &mut ClientState) {
    fail_pending_operations_with(state, LiveTranscriptionClientError::ConnectionFailed);
}

fn fail_pending_operations_with(state: &mut ClientState, error: LiveTranscriptionClientError) {
    if let Some(pending) = state.pending_start.take() {
        let _ = pending.responder.send(Err(error));
    }
    if let Some(pending) = state.pending_chunk.take() {
        let _ = pending.responder.send(Err(error));
    }
    if let Some(pending) = state.pending_end.take() {
        let _ = pending.responder.send(Err(error));
    }
    if let Some(session) = state.session.as_mut() {
        session.in_flight = false;
    }
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
        clear_disconnected, dispatch_message, fail_pending_operations, mark_failed_state,
        validate_chunk_admission, ActiveSession, ClientState, LiveTranscriptionClientError,
        LiveTranscriptionLifecycleStatus, LiveTranscriptionStatus, PendingChunk, PendingEnd,
        PendingStart,
    };
    use crate::{
        assist_mode::events::{
            AssistEventDeliveryError, AssistEventSink, AssistReplySuggestionsEvent,
            AssistSegmentCapability, AssistSegmentUpdateEvent, AssistUpdateState,
            ReplySuggestionEvent, TranscriptSegmentEvent,
        },
        live_transcription::{
            binary_frames::AudioChunkFrameMetadata,
            protocol::{
                AssistModeConfiguration, AudioSource, ChunkResponse, ChunkResultSegment,
                ServerMessage, SessionStarted, SessionStopped,
            },
        },
    };
    use std::sync::{Arc, Mutex as StdMutex};
    use tokio::sync::{oneshot, Mutex};
    use uuid::Uuid;

    #[derive(Default)]
    struct RecordingSink {
        transcript_ids: StdMutex<Vec<Uuid>>,
        segment_updates: StdMutex<Vec<Uuid>>,
        reply_anchors: StdMutex<Vec<Uuid>>,
        fail: bool,
    }

    impl AssistEventSink for RecordingSink {
        fn emit_transcript_segment(
            &self,
            event: TranscriptSegmentEvent,
        ) -> Result<(), AssistEventDeliveryError> {
            if self.fail {
                return Err(AssistEventDeliveryError);
            }
            self.transcript_ids
                .lock()
                .expect("recording lock")
                .push(event.transcript_id);
            Ok(())
        }

        fn emit_segment_update(
            &self,
            event: AssistSegmentUpdateEvent,
        ) -> Result<(), AssistEventDeliveryError> {
            if self.fail {
                return Err(AssistEventDeliveryError);
            }
            self.segment_updates
                .lock()
                .expect("recording lock")
                .push(event.transcript_id);
            Ok(())
        }

        fn emit_reply_suggestions(
            &self,
            event: AssistReplySuggestionsEvent,
        ) -> Result<(), AssistEventDeliveryError> {
            if self.fail {
                return Err(AssistEventDeliveryError);
            }
            self.reply_anchors
                .lock()
                .expect("recording lock")
                .push(event.anchor_transcript_id);
            Ok(())
        }
    }

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
                assist_mode: AssistModeConfiguration {
                    enabled: false,
                    translation_enabled: false,
                    simplification_enabled: false,
                    simplification_level: None,
                    reply_coaching_enabled: false,
                },
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

    #[tokio::test]
    async fn dispatcher_resolves_matching_operation_responders() {
        let state = Arc::new(Mutex::new(ClientState::default()));
        let event_sink = Arc::new(Mutex::new(None));
        let request_id = Uuid::new_v4();
        let session_id = Uuid::new_v4();
        let (start_sender, start_receiver) = oneshot::channel();
        state.lock().await.pending_start = Some(PendingStart {
            request_id,
            responder: start_sender,
        });

        assert!(
            !dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::SessionStarted(SessionStarted {
                    request_id,
                    session_id,
                    expected_sequence: 0,
                })),
            )
            .await
        );
        assert_eq!(
            start_receiver
                .await
                .expect("responder remains connected")
                .expect("matching response succeeds")
                .session_id,
            session_id
        );

        let (end_sender, end_receiver) = oneshot::channel();
        state.lock().await.pending_end = Some(PendingEnd {
            request_id,
            session_id,
            responder: end_sender,
        });
        assert!(
            !dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::SessionStopped(SessionStopped {
                    request_id,
                    session_id,
                })),
            )
            .await
        );
        assert!(end_receiver
            .await
            .expect("responder remains connected")
            .is_ok());
    }

    #[tokio::test]
    async fn dispatcher_does_not_resolve_a_mismatched_chunk() {
        let state = Arc::new(Mutex::new(ClientState::default()));
        let event_sink = Arc::new(Mutex::new(None));
        let (sender, mut receiver) = oneshot::channel();
        state.lock().await.pending_chunk = Some(PendingChunk {
            sequence: 4,
            responder: sender,
        });

        assert!(
            !dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::ChunkResult {
                    chunk_sequence: 5,
                    response: ChunkResponse {
                        skipped_silence: true,
                        accepted_segments: Vec::new(),
                    },
                }),
            )
            .await
        );
        assert!(receiver.try_recv().is_err());
        assert!(state.lock().await.pending_chunk.is_some());
    }

    #[test]
    fn disconnect_cleanup_fails_all_pending_responders() {
        let mut state = ClientState::default();
        let (start_sender, start_receiver) = oneshot::channel();
        let (chunk_sender, chunk_receiver) = oneshot::channel();
        let (end_sender, end_receiver) = oneshot::channel();
        state.pending_start = Some(PendingStart {
            request_id: Uuid::new_v4(),
            responder: start_sender,
        });
        state.pending_chunk = Some(PendingChunk {
            sequence: 0,
            responder: chunk_sender,
        });
        state.pending_end = Some(PendingEnd {
            request_id: Uuid::new_v4(),
            session_id: Uuid::new_v4(),
            responder: end_sender,
        });

        fail_pending_operations(&mut state);

        assert_eq!(
            start_receiver
                .blocking_recv()
                .expect("sender resolves")
                .err()
                .expect("disconnect fails start"),
            LiveTranscriptionClientError::ConnectionFailed
        );
        assert_eq!(
            chunk_receiver
                .blocking_recv()
                .expect("sender resolves")
                .err()
                .expect("disconnect fails chunk"),
            LiveTranscriptionClientError::ConnectionFailed
        );
        assert_eq!(
            end_receiver
                .blocking_recv()
                .expect("sender resolves")
                .err()
                .expect("disconnect fails end"),
            LiveTranscriptionClientError::ConnectionFailed
        );
    }

    #[tokio::test]
    async fn chunk_result_emits_transcripts_in_order_before_resolving() {
        let state = Arc::new(Mutex::new(ClientState {
            status: LiveTranscriptionLifecycleStatus::SessionActive,
            ..ClientState::default()
        }));
        let sink = Arc::new(RecordingSink::default());
        let event_sink: Arc<Mutex<Option<Arc<dyn AssistEventSink>>>> =
            Arc::new(Mutex::new(Some(sink.clone())));
        let (sender, receiver) = oneshot::channel();
        state.lock().await.pending_chunk = Some(PendingChunk {
            sequence: 3,
            responder: sender,
        });
        let first_id = Uuid::new_v4();
        let second_id = Uuid::new_v4();

        assert!(
            !dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::ChunkResult {
                    chunk_sequence: 3,
                    response: ChunkResponse {
                        skipped_silence: false,
                        accepted_segments: vec![
                            transcript_segment(first_id),
                            transcript_segment(second_id),
                        ],
                    },
                }),
            )
            .await
        );

        assert_eq!(
            *sink.transcript_ids.lock().expect("recording lock"),
            vec![first_id, second_id]
        );
        assert!(receiver
            .await
            .expect("chunk responder remains connected")
            .is_ok());
    }

    #[tokio::test]
    async fn assist_events_route_while_a_chunk_is_pending() {
        let state = Arc::new(Mutex::new(ClientState {
            status: LiveTranscriptionLifecycleStatus::SessionActive,
            ..ClientState::default()
        }));
        let sink = Arc::new(RecordingSink::default());
        let event_sink: Arc<Mutex<Option<Arc<dyn AssistEventSink>>>> =
            Arc::new(Mutex::new(Some(sink.clone())));
        let (sender, _receiver) = oneshot::channel();
        state.lock().await.pending_chunk = Some(PendingChunk {
            sequence: 0,
            responder: sender,
        });
        let transcript_id = Uuid::new_v4();
        let anchor_id = Uuid::new_v4();

        assert!(
            !dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::AssistSegmentUpdate(
                    AssistSegmentUpdateEvent {
                        transcript_id,
                        capability: AssistSegmentCapability::Translation,
                        state: AssistUpdateState::Ready,
                        translated_text: Some("translated".to_owned()),
                        simplified_text: None,
                        target_level: None,
                        message: None,
                    }
                )),
            )
            .await
        );
        assert!(
            !dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::AssistReplySuggestions(
                    AssistReplySuggestionsEvent {
                        anchor_transcript_id: anchor_id,
                        state: AssistUpdateState::Ready,
                        suggestions: vec![ReplySuggestionEvent {
                            text: "reply".to_owned(),
                            tone: "professional".to_owned(),
                        }],
                        message: None,
                    },
                )),
            )
            .await
        );

        assert_eq!(
            *sink.segment_updates.lock().expect("recording lock"),
            vec![transcript_id]
        );
        assert_eq!(
            *sink.reply_anchors.lock().expect("recording lock"),
            vec![anchor_id]
        );
        assert!(state.lock().await.pending_chunk.is_some());
    }

    #[tokio::test]
    async fn event_delivery_failure_fails_the_client_without_content() {
        let state = Arc::new(Mutex::new(ClientState {
            status: LiveTranscriptionLifecycleStatus::SessionActive,
            ..ClientState::default()
        }));
        let sink = Arc::new(RecordingSink {
            fail: true,
            ..RecordingSink::default()
        });
        let event_sink: Arc<Mutex<Option<Arc<dyn AssistEventSink>>>> =
            Arc::new(Mutex::new(Some(sink)));
        let (sender, receiver) = oneshot::channel();
        state.lock().await.pending_chunk = Some(PendingChunk {
            sequence: 0,
            responder: sender,
        });

        assert!(
            dispatch_message(
                &state,
                &event_sink,
                Ok(ServerMessage::ChunkResult {
                    chunk_sequence: 0,
                    response: ChunkResponse {
                        skipped_silence: false,
                        accepted_segments: vec![transcript_segment(Uuid::new_v4())],
                    },
                }),
            )
            .await
        );
        assert_eq!(
            state.lock().await.status,
            LiveTranscriptionLifecycleStatus::Failed
        );
        assert_eq!(
            receiver
                .await
                .expect("chunk responder resolves")
                .err()
                .expect("event failure fails chunk"),
            LiveTranscriptionClientError::ConnectionFailed
        );
    }

    fn transcript_segment(transcript_id: Uuid) -> ChunkResultSegment {
        ChunkResultSegment {
            transcript_id,
            text: "private transcript".to_owned(),
            timestamp: "2026-08-02T10:00:00Z".to_owned(),
            source: AudioSource::Mixed,
            speaker: "Unknown".to_owned(),
        }
    }
}
