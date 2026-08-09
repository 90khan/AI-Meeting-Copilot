use std::sync::Arc;

use reqwest::StatusCode;
use tauri::State;
use tokio::sync::{mpsc, oneshot, Mutex};
use uuid::Uuid;

use super::protocol::{
    PlaybackFrame, PlaybackFrameDecoder, PlaybackFrameKind, PlaybackProtocolError,
};
use super::types::{
    BackendPlaybackInfo, PlaybackAudioChunk, RecordingPlaybackInfo, RecordingPlaybackState,
    RecordingPlaybackStatus,
};
use crate::sidecar::manager::SidecarManager;

const PLAYBACK_AUDIO_BUFFER_CAPACITY: usize = 3;

struct PlaybackState {
    state: RecordingPlaybackState,
    info: Option<RecordingPlaybackInfo>,
    meeting_id: Option<Uuid>,
    stream_task: Option<tokio::task::JoinHandle<()>>,
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
            audio_receiver: None,
            stream_generation: 0,
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
        let (task, receiver) = {
            let mut state = self.state.lock().await;
            state.state = RecordingPlaybackState::Stopping;
            state.stream_generation = state.stream_generation.wrapping_add(1);
            let task = state.stream_task.take();
            let receiver = state.audio_receiver.take();
            state.info = None;
            state.meeting_id = None;
            state.state = RecordingPlaybackState::Stopped;
            (task, receiver)
        };
        if let Some(task) = task {
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
    pub async fn start_streaming(&self) -> Result<(), ()> {
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
            let result = consume_playback_stream(connection, meeting_id, sender).await;
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
}

async fn consume_playback_stream(
    connection: crate::sidecar::manager::SidecarConnection,
    meeting_id: Uuid,
    sender: mpsc::Sender<PlaybackAudioChunk>,
) -> Result<(), PlaybackProtocolError> {
    let response = reqwest::Client::new()
        .get(format!(
            "http://{}:{}/api/v1/internal/recordings/{meeting_id}/playback-stream",
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
    let mut previous_index = None;
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
        process_frame, process_stream_bytes, PlaybackState, RecordingPlaybackManager,
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
) -> Result<(), String> {
    manager
        .start_streaming()
        .await
        .map_err(|_| "Playback is unavailable.".to_owned())
}
#[tauri::command]
pub async fn get_recording_playback_status(
    manager: State<'_, RecordingPlaybackManager>,
) -> Result<RecordingPlaybackStatus, String> {
    Ok(manager.status().await)
}
