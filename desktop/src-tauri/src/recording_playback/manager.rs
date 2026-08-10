use std::sync::Arc;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use reqwest::StatusCode;
use serde::Serialize;
use tauri::{Emitter, State};
use tokio::sync::{mpsc, oneshot, Mutex};
use uuid::Uuid;

use super::protocol::{
    PlaybackFrame, PlaybackFrameDecoder, PlaybackFrameKind, PlaybackProtocolError,
};
use super::types::{
    BackendPlaybackInfo, BackendPlaybackSeekResolution, PlaybackAudioChunk, RecordingPlaybackInfo,
    RecordingPlaybackSeekResult, RecordingPlaybackState, RecordingPlaybackStatus,
};
use crate::sidecar::manager::SidecarManager;

const PLAYBACK_AUDIO_BUFFER_CAPACITY: usize = 3;
const PLAYBACK_CHUNK_EVENT: &str = "playback://chunk";
const PLAYBACK_ENDED_EVENT: &str = "playback://ended";
const PLAYBACK_FAILED_EVENT: &str = "playback://failed";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PlaybackChunkEvent {
    generation: u64,
    segment_index: u32,
    /// One self-contained WAV segment only. The full recording is never
    /// accumulated or serialised as one value.
    wav_payload_base64: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PlaybackTerminalEvent {
    generation: u64,
}

pub(crate) trait PlaybackEventSink: Send + Sync {
    fn emit_chunk(&self, event: PlaybackChunkEvent) -> Result<(), ()>;
    fn emit_ended(&self, event: PlaybackTerminalEvent) -> Result<(), ()>;
    fn emit_failed(&self, event: PlaybackTerminalEvent) -> Result<(), ()>;
}

#[derive(Clone)]
pub struct TauriPlaybackEventSink {
    app_handle: tauri::AppHandle,
}

impl TauriPlaybackEventSink {
    pub fn new(app_handle: tauri::AppHandle) -> Self {
        Self { app_handle }
    }
}

impl PlaybackEventSink for TauriPlaybackEventSink {
    fn emit_chunk(&self, event: PlaybackChunkEvent) -> Result<(), ()> {
        self.app_handle
            .emit(PLAYBACK_CHUNK_EVENT, event)
            .map_err(|_| ())
    }

    fn emit_ended(&self, event: PlaybackTerminalEvent) -> Result<(), ()> {
        self.app_handle
            .emit(PLAYBACK_ENDED_EVENT, event)
            .map_err(|_| ())
    }

    fn emit_failed(&self, event: PlaybackTerminalEvent) -> Result<(), ()> {
        self.app_handle
            .emit(PLAYBACK_FAILED_EVENT, event)
            .map_err(|_| ())
    }
}

struct PlaybackState {
    state: RecordingPlaybackState,
    info: Option<RecordingPlaybackInfo>,
    meeting_id: Option<Uuid>,
    stream_task: Option<tokio::task::JoinHandle<()>>,
    event_task: Option<tokio::task::JoinHandle<()>>,
    audio_receiver: Option<Arc<Mutex<mpsc::Receiver<PlaybackAudioChunk>>>>,
    stream_generation: u64,
}
impl Default for PlaybackState {
    fn default() -> Self {
        Self {
            state: RecordingPlaybackState::Idle,
            info: None,
            meeting_id: None,
            stream_task: None,
            event_task: None,
            audio_receiver: None,
            stream_generation: 0,
        }
    }
}

#[derive(Clone)]
pub struct RecordingPlaybackManager {
    state: Arc<Mutex<PlaybackState>>,
    sidecar: SidecarManager,
    event_sink: Arc<Mutex<Option<Arc<dyn PlaybackEventSink>>>>,
}

impl RecordingPlaybackManager {
    pub fn new(sidecar: SidecarManager) -> Self {
        Self {
            state: Arc::new(Mutex::new(PlaybackState::default())),
            sidecar,
            event_sink: Arc::new(Mutex::new(None)),
        }
    }

    pub(crate) fn set_event_sink(&self, sink: Arc<dyn PlaybackEventSink>) {
        let event_sink = Arc::clone(&self.event_sink);
        tauri::async_runtime::block_on(async move { *event_sink.lock().await = Some(sink) });
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
        let (task, event_task, receiver) = {
            let mut state = self.state.lock().await;
            state.state = RecordingPlaybackState::Stopping;
            state.stream_generation = state.stream_generation.wrapping_add(1);
            let task = state.stream_task.take();
            let event_task = state.event_task.take();
            let receiver = state.audio_receiver.take();
            state.info = None;
            state.meeting_id = None;
            state.state = RecordingPlaybackState::Stopped;
            (task, event_task, receiver)
        };
        if let Some(task) = task {
            task.abort();
            let _ = task.await;
        }
        if let Some(task) = event_task {
            task.abort();
            let _ = task.await;
        }
        if let Some(receiver) = receiver {
            clear_buffered_audio(&receiver).await;
        }
        self.status().await
    }

    /// Opens a fresh, authenticated stream for the prepared recording. The
    /// receiver is deliberately internal until the player transport exists.
    pub async fn start_streaming(&self) -> Result<u64, ()> {
        self.start_streaming_from(0).await
    }

    async fn start_streaming_from(&self, start_segment: u32) -> Result<u64, ()> {
        let meeting_id = {
            let mut state = self.state.lock().await;
            if state.state != RecordingPlaybackState::Ready || state.stream_task.is_some() {
                return Err(());
            }
            let meeting_id = state.meeting_id.ok_or(())?;
            state.state = RecordingPlaybackState::Starting;
            meeting_id
        };
        let connection = match self.sidecar.live_transcription_connection().await {
            Ok(connection) => connection,
            Err(_) => {
                let mut state = self.state.lock().await;
                state.state = RecordingPlaybackState::Failed;
                return Err(());
            }
        };
        let (sender, receiver) = mpsc::channel(PLAYBACK_AUDIO_BUFFER_CAPACITY);
        let receiver = Arc::new(Mutex::new(receiver));
        let (generation, previous_receiver) = {
            let mut state = self.state.lock().await;
            if state.state != RecordingPlaybackState::Starting {
                return Err(());
            }
            state.stream_generation = state.stream_generation.wrapping_add(1);
            let generation = state.stream_generation;
            let previous_receiver = state.audio_receiver.replace(receiver);
            state.state = RecordingPlaybackState::Streaming;
            (generation, previous_receiver)
        };
        if let Some(receiver) = previous_receiver {
            clear_buffered_audio(&receiver).await;
        }
        let (launch_sender, launch_receiver) = oneshot::channel();
        let state = Arc::clone(&self.state);
        let task = tokio::spawn(async move {
            if launch_receiver.await.is_err() {
                return;
            }
            let result =
                consume_playback_stream(connection, meeting_id, start_segment, sender).await;
            finish_stream(state, generation, result).await;
        });
        let mut state = self.state.lock().await;
        if state.stream_generation != generation || state.state != RecordingPlaybackState::Streaming
        {
            task.abort();
            return Err(());
        }
        state.stream_task = Some(task);
        drop(state);
        let _ = launch_sender.send(());
        Ok(generation)
    }

    /// Resolve a target through the sidecar, then restart only from its containing segment.
    pub async fn seek(&self, target_seconds: f64) -> Result<RecordingPlaybackSeekResult, ()> {
        if !target_seconds.is_finite() || target_seconds < 0.0 {
            return Err(());
        }
        let meeting_id = {
            let state = self.state.lock().await;
            if !matches!(
                state.state,
                RecordingPlaybackState::Ready | RecordingPlaybackState::Streaming
            ) {
                return Err(());
            }
            state.meeting_id.ok_or(())?
        };
        let mut result = self.fetch_seek(meeting_id, target_seconds).await?;
        self.stop_active_stream().await;
        if result.at_end {
            return Ok(result);
        }
        let start_segment = result.segment_index.ok_or(())?;
        result.generation = Some(self.start_streaming_from(start_segment).await?);
        Ok(result)
    }

    async fn stop_active_stream(&self) {
        let (task, event_task, receiver) = {
            let mut state = self.state.lock().await;
            state.stream_generation = state.stream_generation.wrapping_add(1);
            let task = state.stream_task.take();
            let event_task = state.event_task.take();
            let receiver = state.audio_receiver.take();
            if state.meeting_id.is_some() {
                state.state = RecordingPlaybackState::Ready;
            }
            (task, event_task, receiver)
        };
        if let Some(task) = task {
            task.abort();
            let _ = task.await;
        }
        if let Some(task) = event_task {
            task.abort();
            let _ = task.await;
        }
        if let Some(receiver) = receiver {
            clear_buffered_audio(&receiver).await;
        }
    }

    /// Activates the Tauri event pump only after its opaque generation has
    /// been returned to the WebView. This prevents an early first chunk from
    /// being mistaken for a stale event by the player.
    pub async fn activate_event_pump(&self, generation: u64) -> Result<(), ()> {
        if self.event_sink.lock().await.is_none() {
            return Err(());
        }
        let (receiver, state) = {
            let state = self.state.lock().await;
            if state.stream_generation != generation
                || state.state != RecordingPlaybackState::Streaming
                || state.event_task.is_some()
            {
                return Err(());
            }
            let receiver = state.audio_receiver.clone().ok_or(())?;
            (receiver, Arc::clone(&self.state))
        };
        let event_sink = Arc::clone(&self.event_sink);
        let task = tokio::spawn(async move {
            pump_playback_events(state, event_sink, receiver, generation).await;
        });
        let mut state = self.state.lock().await;
        if state.stream_generation != generation
            || state.state != RecordingPlaybackState::Streaming
            || state.event_task.is_some()
        {
            task.abort();
            return Err(());
        }
        state.event_task = Some(task);
        Ok(())
    }

    /// The future player transport consumes one chunk at a time. It is not a
    /// Tauri command and never serializes plaintext audio to the frontend.
    #[allow(dead_code)]
    pub(crate) async fn next_audio_chunk(&self) -> Option<PlaybackAudioChunk> {
        let receiver = {
            let state = self.state.lock().await;
            state.audio_receiver.clone()
        }?;
        let chunk = receiver.lock().await.recv().await;
        chunk
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

    async fn fetch_seek(
        &self,
        meeting_id: Uuid,
        target_seconds: f64,
    ) -> Result<RecordingPlaybackSeekResult, ()> {
        let connection = self
            .sidecar
            .live_transcription_connection()
            .await
            .map_err(|_| ())?;
        let response = reqwest::Client::new()
            .get(format!(
                "http://{}:{}/api/v1/internal/recordings/{meeting_id}/seek",
                connection.host, connection.port
            ))
            .query(&[("target_seconds", target_seconds)])
            .header("x-ai-meeting-copilot-token", connection.token)
            .send()
            .await
            .map_err(|_| ())?;
        if response.status() != StatusCode::OK {
            return Err(());
        }
        let body = response.text().await.map_err(|_| ())?;
        let response =
            serde_json::from_str::<BackendPlaybackSeekResolution>(&body).map_err(|_| ())?;
        RecordingPlaybackSeekResult::try_from(response).map_err(|_| ())
    }
}

/// The only bridge that serialises plaintext segments to the WebView. It
/// receives at most the existing three queued chunks and encodes one segment
/// at a time for Tauri's JSON event transport.
async fn pump_playback_events(
    state: Arc<Mutex<PlaybackState>>,
    event_sink: Arc<Mutex<Option<Arc<dyn PlaybackEventSink>>>>,
    receiver: Arc<Mutex<mpsc::Receiver<PlaybackAudioChunk>>>,
    generation: u64,
) {
    loop {
        let chunk = receiver.lock().await.recv().await;
        let Some(chunk) = chunk else {
            let outcome = {
                let state = state.lock().await;
                if state.stream_generation != generation {
                    return;
                }
                match state.state {
                    // The stream task updates this to Ready before dropping
                    // its sender. Streaming is retained as a defensive
                    // clean-close case for a receiver whose sender has ended
                    // immediately after the final frame.
                    RecordingPlaybackState::Ready | RecordingPlaybackState::Streaming => Some(true),
                    RecordingPlaybackState::Failed => Some(false),
                    _ => None,
                }
            };
            let Some(clean_end) = outcome else { return };
            let sink = event_sink.lock().await.clone();
            if let Some(sink) = sink {
                let event = PlaybackTerminalEvent { generation };
                let _ = if clean_end {
                    sink.emit_ended(event)
                } else {
                    sink.emit_failed(event)
                };
            }
            finish_event_pump(Arc::clone(&state), generation, clean_end).await;
            return;
        };
        let active = {
            let state = state.lock().await;
            state.stream_generation == generation
                && state.state == RecordingPlaybackState::Streaming
        };
        if !active {
            return;
        }
        let sink = event_sink.lock().await.clone();
        let Some(sink) = sink else { continue };
        if sink
            .emit_chunk(PlaybackChunkEvent {
                generation,
                segment_index: chunk.segment_index,
                wav_payload_base64: BASE64.encode(chunk.bytes),
            })
            .is_err()
        {
            fail_event_delivery(Arc::clone(&state), generation).await;
            return;
        }
    }
}

async fn finish_event_pump(state: Arc<Mutex<PlaybackState>>, generation: u64, clean_end: bool) {
    let mut state = state.lock().await;
    if state.stream_generation != generation {
        return;
    }
    state.event_task.take();
    if clean_end && state.state == RecordingPlaybackState::Ready {
        state.state = RecordingPlaybackState::Stopped;
    }
}

async fn fail_event_delivery(state: Arc<Mutex<PlaybackState>>, generation: u64) {
    let receiver = {
        let mut state = state.lock().await;
        if state.stream_generation != generation {
            return;
        }
        state.state = RecordingPlaybackState::Failed;
        state.audio_receiver.take()
    };
    if let Some(receiver) = receiver {
        clear_buffered_audio(&receiver).await;
    }
}

async fn consume_playback_stream(
    connection: crate::sidecar::manager::SidecarConnection,
    meeting_id: Uuid,
    start_segment: u32,
    sender: mpsc::Sender<PlaybackAudioChunk>,
) -> Result<(), PlaybackProtocolError> {
    let response = reqwest::Client::new()
        .get(format!(
            "http://{}:{}/api/v1/internal/recordings/{meeting_id}/playback-stream?start_segment={start_segment}",
            connection.host, connection.port
        ))
        .header("x-ai-meeting-copilot-token", connection.token)
        .send()
        .await
        .map_err(|_| PlaybackProtocolError)?;
    if response.status() != StatusCode::OK {
        return Err(PlaybackProtocolError);
    }
    let mut response = response;
    let mut decoder = PlaybackFrameDecoder::new();
    let mut previous_index = start_segment.checked_sub(1);
    loop {
        let bytes = response.chunk().await.map_err(|_| PlaybackProtocolError)?;
        let Some(bytes) = bytes else {
            decoder.finish()?;
            return Err(PlaybackProtocolError);
        };
        if process_stream_bytes(&mut decoder, &bytes, &sender, &mut previous_index).await? {
            return Ok(());
        }
    }
}

/// Processes one arbitrary HTTP body fragment. It is also the deterministic
/// byte-stream seam used by unit tests; it does not know about HTTP or secrets.
async fn process_stream_bytes(
    decoder: &mut PlaybackFrameDecoder,
    bytes: &[u8],
    sender: &mpsc::Sender<PlaybackAudioChunk>,
    previous_index: &mut Option<u32>,
) -> Result<bool, PlaybackProtocolError> {
    decoder.push(bytes);
    while let Some(frame) = decoder.next_frame()? {
        if process_frame(frame, sender, previous_index).await? {
            // Terminal frames end this stream immediately. Any subsequent
            // bytes already buffered are rejected; later transport bytes are
            // intentionally not read after this terminal boundary.
            decoder.finish()?;
            return Ok(true);
        }
    }
    Ok(false)
}

/// Returns true only for a clean, terminal END frame.
async fn process_frame(
    frame: PlaybackFrame,
    sender: &mpsc::Sender<PlaybackAudioChunk>,
    previous_index: &mut Option<u32>,
) -> Result<bool, PlaybackProtocolError> {
    match frame.header.kind {
        PlaybackFrameKind::AudioSegment => {
            if previous_index.is_some_and(|index| frame.header.segment_index <= index) {
                return Err(PlaybackProtocolError);
            }
            *previous_index = Some(frame.header.segment_index);
            sender
                .send(PlaybackAudioChunk {
                    segment_index: frame.header.segment_index,
                    bytes: frame.payload,
                })
                .await
                .map_err(|_| PlaybackProtocolError)?;
            Ok(false)
        }
        PlaybackFrameKind::End => Ok(true),
        PlaybackFrameKind::Error => Err(PlaybackProtocolError),
    }
}

async fn finish_stream(
    state: Arc<Mutex<PlaybackState>>,
    generation: u64,
    result: Result<(), PlaybackProtocolError>,
) {
    let receiver = {
        let mut state = state.lock().await;
        if state.stream_generation != generation {
            return;
        }
        state.stream_task = None;
        state.state = if result.is_ok() {
            RecordingPlaybackState::Ready
        } else {
            RecordingPlaybackState::Failed
        };
        if result.is_err() {
            state.audio_receiver.take()
        } else {
            None
        }
    };
    if result.is_err() {
        if let Some(receiver) = receiver {
            clear_buffered_audio(&receiver).await;
        }
    }
}

async fn clear_buffered_audio(receiver: &Arc<Mutex<mpsc::Receiver<PlaybackAudioChunk>>>) {
    let mut receiver = receiver.lock().await;
    while receiver.try_recv().is_ok() {}
}

#[cfg(test)]
mod tests {
    use super::{
        process_frame, process_stream_bytes, pump_playback_events, PlaybackChunkEvent,
        PlaybackEventSink, PlaybackState, PlaybackTerminalEvent, RecordingPlaybackManager,
        PLAYBACK_AUDIO_BUFFER_CAPACITY,
    };
    use crate::recording_playback::protocol::{
        PlaybackFrame, PlaybackFrameDecoder, PlaybackFrameHeader, PlaybackFrameKind,
    };
    use crate::recording_playback::types::{
        PlaybackAudioChunk, RecordingPlaybackInfo, RecordingPlaybackState,
    };
    use crate::sidecar::manager::SidecarManager;
    use std::sync::Arc;
    use tokio::sync::{mpsc, Mutex};
    use uuid::Uuid;

    fn frame(kind: PlaybackFrameKind, segment_index: u32, payload: &[u8]) -> PlaybackFrame {
        PlaybackFrame {
            header: PlaybackFrameHeader {
                version: 1,
                kind,
                segment_index,
                payload_length: payload.len() as u32,
            },
            payload: payload.to_vec(),
        }
    }

    fn encoded_frame(kind: u8, segment_index: u32, payload: &[u8]) -> Vec<u8> {
        let mut bytes = vec![1, kind];
        bytes.extend_from_slice(&segment_index.to_be_bytes());
        bytes.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        bytes.extend_from_slice(payload);
        bytes
    }

    #[derive(Default)]
    struct FakeEventSink {
        events: std::sync::Mutex<Vec<(String, u64, Option<u32>)>>,
    }

    impl PlaybackEventSink for FakeEventSink {
        fn emit_chunk(&self, event: PlaybackChunkEvent) -> Result<(), ()> {
            self.events.lock().expect("test event lock").push((
                "chunk".to_owned(),
                event.generation,
                Some(event.segment_index),
            ));
            assert!(!event.wav_payload_base64.contains("sensitive audio bytes"));
            Ok(())
        }

        fn emit_ended(&self, event: PlaybackTerminalEvent) -> Result<(), ()> {
            self.events.lock().expect("test event lock").push((
                "ended".to_owned(),
                event.generation,
                None,
            ));
            Ok(())
        }

        fn emit_failed(&self, event: PlaybackTerminalEvent) -> Result<(), ()> {
            self.events.lock().expect("test event lock").push((
                "failed".to_owned(),
                event.generation,
                None,
            ));
            Ok(())
        }
    }

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
                    format: "wav_pcm16_mono_16khz_segmented_v1".to_owned(),
                    capture_anchor_utc: "2026-01-01T00:00:00Z".to_owned(),
                    duration_seconds: Some(1.0),
                    segment_count: 1,
                    has_gaps: false,
                }),
                meeting_id: Some(meeting_id),
                stream_task: None,
                event_task: None,
                audio_receiver: None,
                stream_generation: 0,
            };
        }

        assert!(manager.start(Uuid::new_v4()).await.is_err());
        assert_eq!(manager.status().await.state, RecordingPlaybackState::Ready);
    }

    #[tokio::test]
    async fn stream_frames_preserve_order_and_allow_gaps() {
        let (sender, mut receiver) = mpsc::channel(3);
        let mut previous_index = None;

        assert!(!process_frame(
            frame(PlaybackFrameKind::AudioSegment, 2, b"first"),
            &sender,
            &mut previous_index,
        )
        .await
        .expect("first frame is valid"));
        assert!(!process_frame(
            frame(PlaybackFrameKind::AudioSegment, 7, b"second"),
            &sender,
            &mut previous_index,
        )
        .await
        .expect("gaps are valid"));

        let first = receiver.recv().await.expect("first audio chunk");
        let second = receiver.recv().await.expect("second audio chunk");
        assert_eq!((first.segment_index, first.bytes), (2, b"first".to_vec()));
        assert_eq!(
            (second.segment_index, second.bytes),
            (7, b"second".to_vec())
        );
    }

    #[tokio::test]
    async fn fragmented_byte_stream_preserves_multiple_audio_frames_and_clean_end() {
        let (sender, mut receiver) = mpsc::channel(3);
        let mut decoder = PlaybackFrameDecoder::new();
        let mut previous_index = None;
        let mut bytes = encoded_frame(1, 1, b"first");
        bytes.extend(encoded_frame(1, 4, b"second"));
        bytes.extend(encoded_frame(2, 5, b""));

        assert!(
            !process_stream_bytes(&mut decoder, &bytes[..7], &sender, &mut previous_index,)
                .await
                .expect("fragment is valid")
        );
        assert!(
            process_stream_bytes(&mut decoder, &bytes[7..], &sender, &mut previous_index,)
                .await
                .expect("stream ends cleanly")
        );
        assert_eq!(receiver.recv().await.expect("first").segment_index, 1);
        assert_eq!(receiver.recv().await.expect("second").segment_index, 4);
    }

    #[tokio::test]
    async fn duplicate_or_backward_stream_indexes_are_rejected() {
        let (sender, _receiver) = mpsc::channel(3);
        let mut previous_index = None;
        process_frame(
            frame(PlaybackFrameKind::AudioSegment, 3, b"audio"),
            &sender,
            &mut previous_index,
        )
        .await
        .expect("first index is valid");

        assert!(process_frame(
            frame(PlaybackFrameKind::AudioSegment, 3, b"duplicate"),
            &sender,
            &mut previous_index,
        )
        .await
        .is_err());
        assert!(process_frame(
            frame(PlaybackFrameKind::AudioSegment, 2, b"backward"),
            &sender,
            &mut previous_index,
        )
        .await
        .is_err());
    }

    #[tokio::test]
    async fn end_and_error_frames_have_distinct_internal_results() {
        let (sender, _receiver) = mpsc::channel(1);
        let mut previous_index = None;

        assert!(process_frame(
            frame(PlaybackFrameKind::End, 1, b""),
            &sender,
            &mut previous_index,
        )
        .await
        .expect("end is clean"));
        assert!(process_frame(
            frame(PlaybackFrameKind::Error, 1, b""),
            &sender,
            &mut previous_index,
        )
        .await
        .is_err());
    }

    #[tokio::test]
    async fn next_audio_chunk_delivers_one_bounded_chunk_at_a_time() {
        let manager = RecordingPlaybackManager::new(SidecarManager::new());
        let (sender, receiver) = mpsc::channel(1);
        sender
            .send(PlaybackAudioChunk {
                segment_index: 4,
                bytes: b"audio".to_vec(),
            })
            .await
            .expect("receiver is active");
        manager.state.lock().await.audio_receiver = Some(Arc::new(Mutex::new(receiver)));

        let chunk = manager
            .next_audio_chunk()
            .await
            .expect("one chunk is delivered");
        assert_eq!(chunk.segment_index, 4);
        assert_eq!(chunk.bytes, b"audio");
    }

    #[tokio::test]
    async fn full_buffer_applies_backpressure_and_task_cancellation_is_prompt() {
        let (sender, mut receiver) = mpsc::channel(1);
        sender
            .send(PlaybackAudioChunk {
                segment_index: 1,
                bytes: b"first".to_vec(),
            })
            .await
            .expect("receiver is active");
        let blocked_sender = sender.clone();
        let task = tokio::spawn(async move {
            let mut previous_index = None;
            process_frame(
                frame(PlaybackFrameKind::AudioSegment, 2, b"second"),
                &blocked_sender,
                &mut previous_index,
            )
            .await
        });

        tokio::task::yield_now().await;
        assert!(!task.is_finished());
        task.abort();
        assert!(task.await.is_err());
        assert_eq!(
            receiver
                .recv()
                .await
                .expect("first chunk remains")
                .segment_index,
            1
        );
    }

    #[tokio::test]
    async fn event_pump_preserves_chunk_order_and_emits_one_clean_end() {
        let state = Arc::new(Mutex::new(PlaybackState {
            state: RecordingPlaybackState::Streaming,
            stream_generation: 6,
            ..PlaybackState::default()
        }));
        let sink = Arc::new(FakeEventSink::default());
        let sink_state: Arc<Mutex<Option<Arc<dyn PlaybackEventSink>>>> =
            Arc::new(Mutex::new(Some(sink.clone())));
        let (sender, receiver) = mpsc::channel(PLAYBACK_AUDIO_BUFFER_CAPACITY);
        sender
            .send(PlaybackAudioChunk {
                segment_index: 2,
                bytes: b"one".to_vec(),
            })
            .await
            .expect("receiver active");
        sender
            .send(PlaybackAudioChunk {
                segment_index: 4,
                bytes: b"two".to_vec(),
            })
            .await
            .expect("receiver active");
        drop(sender);
        pump_playback_events(state, sink_state, Arc::new(Mutex::new(receiver)), 6).await;

        assert_eq!(
            *sink.events.lock().expect("test event lock"),
            vec![
                ("chunk".to_owned(), 6, Some(2)),
                ("chunk".to_owned(), 6, Some(4)),
                ("ended".to_owned(), 6, None),
            ]
        );
    }

    #[tokio::test]
    async fn stale_event_pump_does_not_emit_after_a_new_generation() {
        let state = Arc::new(Mutex::new(PlaybackState {
            state: RecordingPlaybackState::Stopped,
            stream_generation: 8,
            ..PlaybackState::default()
        }));
        let sink = Arc::new(FakeEventSink::default());
        let event_sink: Arc<Mutex<Option<Arc<dyn PlaybackEventSink>>>> =
            Arc::new(Mutex::new(Some(sink.clone())));
        let (sender, receiver) = mpsc::channel(1);
        drop(sender);
        pump_playback_events(state, event_sink, Arc::new(Mutex::new(receiver)), 7).await;
        assert!(sink.events.lock().expect("test event lock").is_empty());
    }

    #[test]
    fn audio_chunks_redact_plaintext_from_debug_output_and_buffer_is_small() {
        let chunk = PlaybackAudioChunk {
            segment_index: 9,
            bytes: b"sensitive audio bytes".to_vec(),
        };

        let debug = format!("{chunk:?}");
        assert!(PLAYBACK_AUDIO_BUFFER_CAPACITY <= 4);
        assert!(!debug.contains("sensitive audio bytes"));
        assert!(debug.contains("byte_length"));
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
pub async fn start_recording_playback_stream(
    manager: State<'_, RecordingPlaybackManager>,
) -> Result<u64, String> {
    manager
        .start_streaming()
        .await
        .map_err(|_| "Playback is unavailable.".to_owned())
}

#[tauri::command]
pub async fn seek_recording_playback(
    target_seconds: f64,
    manager: State<'_, RecordingPlaybackManager>,
) -> Result<RecordingPlaybackSeekResult, ()> {
    manager.seek(target_seconds).await
}
#[tauri::command]
pub async fn activate_recording_playback_events(
    manager: State<'_, RecordingPlaybackManager>,
    generation: u64,
) -> Result<(), String> {
    manager
        .activate_event_pump(generation)
        .await
        .map_err(|_| "Playback is unavailable.".to_owned())
}
#[tauri::command]
pub async fn get_recording_playback_status(
    manager: State<'_, RecordingPlaybackManager>,
) -> Result<RecordingPlaybackStatus, String> {
    Ok(manager.status().await)
}
