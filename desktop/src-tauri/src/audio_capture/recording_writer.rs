//! Session-scoped, bounded recording-segment upload without capture ownership.

use std::{
    fmt,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
};

use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::{sync::mpsc, task::JoinHandle};
use uuid::Uuid;

use crate::sidecar::manager::SidecarManager;

// Two queued five-second segments bound plaintext memory. Overflow fails the
// opt-in recording branch explicitly; it never silently drops audio.
const RECORDING_QUEUE_CAPACITY: usize = 2;
const RECORDING_SAMPLE_RATE_HZ: f64 = 16_000.0;

/// Private opt-in configuration. It deliberately has no storage identifiers.
#[derive(Clone, PartialEq, Eq)]
pub(crate) struct RecordingConfiguration {
    pub(crate) enabled: bool,
    pub(crate) consent_confirmed_at: Option<String>,
    pub(crate) retention_policy: RecordingRetentionPolicy,
}

impl RecordingConfiguration {
    pub(crate) fn disabled() -> Self {
        Self {
            enabled: false,
            consent_confirmed_at: None,
            retention_policy: RecordingRetentionPolicy::SevenDays,
        }
    }

    fn validate(&self) -> Result<(), RecordingWriterError> {
        if self.enabled
            && !self
                .consent_confirmed_at
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty() && value.ends_with('Z'))
        {
            return Err(RecordingWriterError::InvalidConfiguration);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RecordingRetentionPolicy {
    OneDay,
    SevenDays,
    ThirtyDays,
    Manual,
}

#[derive(Clone, Copy)]
pub(crate) enum RecordingFailureCode {
    CaptureFailed,
    EncryptionFailed,
    FinalizationFailed,
    Interrupted,
    StorageFailed,
    WriterFailed,
}

impl RecordingFailureCode {
    const fn as_str(self) -> &'static str {
        match self {
            Self::CaptureFailed => "capture_failed",
            Self::EncryptionFailed => "encryption_failed",
            Self::FinalizationFailed => "finalization_failed",
            Self::Interrupted => "interrupted",
            Self::StorageFailed => "storage_failed",
            Self::WriterFailed => "writer_failed",
        }
    }
}

impl RecordingRetentionPolicy {
    const fn as_str(self) -> &'static str {
        match self {
            Self::OneDay => "one_day",
            Self::SevenDays => "seven_days",
            Self::ThirtyDays => "thirty_days",
            Self::Manual => "manual",
        }
    }
}

/// Already encoded, self-contained V1 WAV segment. WAV bytes are redacted in Debug.
pub(crate) struct EncodedRecordingSegment {
    pub(crate) segment_index: u32,
    pub(crate) wav_bytes: Vec<u8>,
    pub(crate) sample_count: usize,
}

impl EncodedRecordingSegment {
    pub(crate) fn new(
        segment_index: u32,
        wav_bytes: Vec<u8>,
        sample_count: usize,
    ) -> Result<Self, RecordingWriterError> {
        if sample_count == 0 || wav_bytes.is_empty() {
            return Err(RecordingWriterError::InvalidSegment);
        }
        Ok(Self {
            segment_index,
            wav_bytes,
            sample_count,
        })
    }
}

impl fmt::Debug for EncodedRecordingSegment {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EncodedRecordingSegment")
            .field("segment_index", &self.segment_index)
            .field("wav_bytes", &"<redacted>")
            .field("sample_count", &self.sample_count)
            .finish()
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum RecordingWriterError {
    #[error("Recording configuration is invalid.")]
    InvalidConfiguration,
    #[error("Recording segment is invalid.")]
    InvalidSegment,
    #[error("Recording writer is already active.")]
    AlreadyActive,
    #[error("Recording writer is unavailable.")]
    Unavailable,
    #[error("Recording segment was rejected.")]
    SegmentRejected,
}

pub(crate) type RecordingFuture<'a, T> =
    Pin<Box<dyn Future<Output = Result<T, RecordingWriterError>> + Send + 'a>>;

/// Trusted, fakeable sidecar boundary. Implementations retain connection secrets.
pub(crate) trait RecordingBackendClient: Send + Sync + 'static {
    fn prepare<'a>(
        &'a self,
        meeting_id: Uuid,
        configuration: &'a RecordingConfiguration,
    ) -> RecordingFuture<'a, PrepareRecordingResponse>;
    fn start<'a>(&'a self, recording_id: Uuid) -> RecordingFuture<'a, ()>;
    fn write_segment<'a>(
        &'a self,
        recording_id: Uuid,
        segment: EncodedRecordingSegment,
    ) -> RecordingFuture<'a, ()>;
    fn finalize<'a>(
        &'a self,
        recording_id: Uuid,
        duration_seconds: f64,
        segment_count: u32,
        has_gaps: bool,
    ) -> RecordingFuture<'a, ()>;
    fn fail<'a>(&'a self, recording_id: Uuid, failure_code: &'a str) -> RecordingFuture<'a, ()>;
}

/// Object-safe recording branch boundary held by the capture coordinator.
pub(crate) trait RecordingSegmentWriter: Send + Sync {
    fn try_enqueue(&self, segment: EncodedRecordingSegment) -> Result<(), RecordingWriterError>;
    fn set_has_gaps(&self, has_gaps: bool);
    fn finalize<'a>(&'a self) -> RecordingFuture<'a, ()>;
    fn abort<'a>(
        &'a self,
        failure_code: RecordingFailureCode,
    ) -> Pin<Box<dyn Future<Output = ()> + Send + 'a>>;
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct PrepareRecordingResponse {
    enabled: bool,
    recording_id: Option<Uuid>,
}

/// Internal sidecar client. Its only connection source is SidecarManager.
pub(crate) struct SidecarRecordingBackendClient {
    sidecar: SidecarManager,
}

impl SidecarRecordingBackendClient {
    pub(crate) fn new(sidecar: SidecarManager) -> Self {
        Self { sidecar }
    }
}

#[derive(Serialize)]
struct PrepareRequest<'a> {
    enabled: bool,
    consent_confirmed_at: Option<&'a str>,
    retention_policy: &'a str,
}

#[derive(Deserialize)]
struct BackendPrepareResponse {
    enabled: bool,
    recording_id: Option<Uuid>,
    state: Option<String>,
}

#[derive(Serialize)]
struct FinalizeRequest {
    duration_seconds: f64,
    segment_count: u32,
    has_gaps: bool,
}

#[derive(Serialize)]
struct FailRequest<'a> {
    failure_code: &'a str,
}

impl RecordingBackendClient for SidecarRecordingBackendClient {
    fn prepare<'a>(
        &'a self,
        meeting_id: Uuid,
        configuration: &'a RecordingConfiguration,
    ) -> RecordingFuture<'a, PrepareRecordingResponse> {
        Box::pin(async move {
            let connection = self
                .sidecar
                .live_transcription_connection()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            let response = reqwest::Client::new()
                .post(format!(
                    "http://{}:{}/api/v1/internal/recordings/{meeting_id}/prepare",
                    connection.host, connection.port
                ))
                .header("x-ai-meeting-copilot-token", connection.token)
                .header("content-type", "application/json")
                .body(
                    serde_json::to_string(&PrepareRequest {
                        enabled: configuration.enabled,
                        consent_confirmed_at: configuration.consent_confirmed_at.as_deref(),
                        retention_policy: configuration.retention_policy.as_str(),
                    })
                    .map_err(|_| RecordingWriterError::Unavailable)?,
                )
                .send()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            if response.status() != StatusCode::OK {
                return Err(RecordingWriterError::Unavailable);
            }
            let body = response
                .text()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            let body = serde_json::from_str::<BackendPrepareResponse>(&body)
                .map_err(|_| RecordingWriterError::Unavailable)?;
            match (body.enabled, body.recording_id, body.state.as_deref()) {
                (false, None, None) => Ok(PrepareRecordingResponse {
                    enabled: false,
                    recording_id: None,
                }),
                (true, Some(recording_id), Some("pending")) => Ok(PrepareRecordingResponse {
                    enabled: true,
                    recording_id: Some(recording_id),
                }),
                _ => Err(RecordingWriterError::Unavailable),
            }
        })
    }

    fn start<'a>(&'a self, recording_id: Uuid) -> RecordingFuture<'a, ()> {
        Box::pin(async move {
            let connection = self
                .sidecar
                .live_transcription_connection()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            let response = reqwest::Client::new()
                .post(format!(
                    "http://{}:{}/api/v1/internal/recordings/{recording_id}/start",
                    connection.host, connection.port
                ))
                .header("x-ai-meeting-copilot-token", connection.token)
                .send()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            if response.status() == StatusCode::OK {
                Ok(())
            } else {
                Err(RecordingWriterError::Unavailable)
            }
        })
    }

    fn write_segment<'a>(
        &'a self,
        recording_id: Uuid,
        segment: EncodedRecordingSegment,
    ) -> RecordingFuture<'a, ()> {
        Box::pin(async move {
            let connection = self
                .sidecar
                .live_transcription_connection()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            let response = reqwest::Client::new()
                .post(format!(
                    "http://{}:{}/api/v1/internal/recordings/{recording_id}/segments/{}",
                    connection.host, connection.port, segment.segment_index
                ))
                .header("x-ai-meeting-copilot-token", connection.token)
                .header("content-type", "audio/wav")
                .body(segment.wav_bytes)
                .send()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            if response.status() == StatusCode::OK {
                Ok(())
            } else {
                Err(RecordingWriterError::SegmentRejected)
            }
        })
    }

    fn finalize<'a>(
        &'a self,
        recording_id: Uuid,
        duration_seconds: f64,
        segment_count: u32,
        has_gaps: bool,
    ) -> RecordingFuture<'a, ()> {
        Box::pin(async move {
            let connection = self
                .sidecar
                .live_transcription_connection()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            let response = reqwest::Client::new()
                .post(format!(
                    "http://{}:{}/api/v1/internal/recordings/{recording_id}/finalize",
                    connection.host, connection.port
                ))
                .header("x-ai-meeting-copilot-token", connection.token)
                .header("content-type", "application/json")
                .body(
                    serde_json::to_string(&FinalizeRequest {
                        duration_seconds,
                        segment_count,
                        has_gaps,
                    })
                    .map_err(|_| RecordingWriterError::Unavailable)?,
                )
                .send()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            if response.status() == StatusCode::OK {
                Ok(())
            } else {
                Err(RecordingWriterError::Unavailable)
            }
        })
    }

    fn fail<'a>(&'a self, recording_id: Uuid, failure_code: &'a str) -> RecordingFuture<'a, ()> {
        Box::pin(async move {
            let connection = self
                .sidecar
                .live_transcription_connection()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            let response = reqwest::Client::new()
                .post(format!(
                    "http://{}:{}/api/v1/internal/recordings/{recording_id}/fail",
                    connection.host, connection.port
                ))
                .header("x-ai-meeting-copilot-token", connection.token)
                .header("content-type", "application/json")
                .body(
                    serde_json::to_string(&FailRequest { failure_code })
                        .map_err(|_| RecordingWriterError::Unavailable)?,
                )
                .send()
                .await
                .map_err(|_| RecordingWriterError::Unavailable)?;
            if response.status() == StatusCode::OK {
                Ok(())
            } else {
                Err(RecordingWriterError::Unavailable)
            }
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum RecordingWriterState {
    Idle,
    Starting,
    Active,
    Finalizing,
    Failed,
}

struct WriterState {
    state: RecordingWriterState,
    recording_id: Option<Uuid>,
    sender: Option<mpsc::Sender<EncodedRecordingSegment>>,
    task: Option<JoinHandle<()>>,
    expected_segment_index: u32,
    persisted_segment_count: u32,
    persisted_sample_count: usize,
    has_gaps: bool,
}

impl Default for WriterState {
    fn default() -> Self {
        Self {
            state: RecordingWriterState::Idle,
            recording_id: None,
            sender: None,
            task: None,
            expected_segment_index: 0,
            persisted_segment_count: 0,
            persisted_sample_count: 0,
            has_gaps: false,
        }
    }
}

/// One recording session. The writer task is the sole owner of backend segment writes.
#[derive(Clone)]
pub(crate) struct RecordingWriter<C: RecordingBackendClient> {
    backend: Arc<C>,
    state: Arc<Mutex<WriterState>>,
}

impl<C: RecordingBackendClient> RecordingWriter<C> {
    pub(crate) fn new(backend: Arc<C>) -> Self {
        Self {
            backend,
            state: Arc::new(Mutex::new(WriterState::default())),
        }
    }

    /// Returns false without contacting the sidecar when recording is disabled.
    pub(crate) async fn prepare_and_start(
        &self,
        meeting_id: Uuid,
        configuration: RecordingConfiguration,
    ) -> Result<bool, RecordingWriterError> {
        configuration.validate()?;
        if !configuration.enabled {
            return Ok(false);
        }
        {
            let mut state = self.state.lock().expect("recording writer state lock");
            if state.state != RecordingWriterState::Idle {
                return Err(RecordingWriterError::AlreadyActive);
            }
            state.state = RecordingWriterState::Starting;
        }
        let prepared = match self.backend.prepare(meeting_id, &configuration).await {
            Ok(prepared) => prepared,
            Err(error) => {
                self.mark_failed(None);
                return Err(error);
            }
        };
        if !prepared.enabled {
            self.reset_idle();
            return Ok(false);
        }
        let recording_id = prepared
            .recording_id
            .ok_or(RecordingWriterError::Unavailable)?;
        if self.backend.start(recording_id).await.is_err() {
            let _ = self.backend.fail(recording_id, "capture_failed").await;
            self.mark_failed(None);
            return Err(RecordingWriterError::Unavailable);
        }

        let (sender, receiver) = mpsc::channel(RECORDING_QUEUE_CAPACITY);
        let state = Arc::clone(&self.state);
        let backend = Arc::clone(&self.backend);
        let task = tokio::spawn(async move {
            run_writer_task(backend, state, recording_id, receiver).await;
        });
        let mut state = self.state.lock().expect("recording writer state lock");
        if state.state != RecordingWriterState::Starting {
            task.abort();
            return Err(RecordingWriterError::Unavailable);
        }
        state.recording_id = Some(recording_id);
        state.sender = Some(sender);
        state.task = Some(task);
        state.state = RecordingWriterState::Active;
        Ok(true)
    }

    /// Nonblocking producer boundary. Full capacity fails recording rather than drops audio.
    pub(crate) fn try_enqueue(
        &self,
        segment: EncodedRecordingSegment,
    ) -> Result<(), RecordingWriterError> {
        let (sender, recording_id) = {
            let state = self.state.lock().expect("recording writer state lock");
            if state.state != RecordingWriterState::Active {
                return Err(RecordingWriterError::Unavailable);
            }
            (state.sender.clone(), state.recording_id)
        };
        let sender = sender.ok_or(RecordingWriterError::Unavailable)?;
        match sender.try_send(segment) {
            Ok(()) => Ok(()),
            Err(mpsc::error::TrySendError::Full(_)) => {
                self.fail_without_waiting(recording_id, "writer_failed");
                Err(RecordingWriterError::Unavailable)
            }
            Err(mpsc::error::TrySendError::Closed(_)) => Err(RecordingWriterError::Unavailable),
        }
    }

    pub(crate) fn set_has_gaps(&self, has_gaps: bool) {
        if has_gaps {
            self.state
                .lock()
                .expect("recording writer state lock")
                .has_gaps = true;
        }
    }

    /// Drain queued accepted segments, then persist exact audio-sample duration.
    pub(crate) async fn finalize(&self) -> Result<(), RecordingWriterError> {
        let (recording_id, task) = {
            let mut state = self.state.lock().expect("recording writer state lock");
            if state.state != RecordingWriterState::Active {
                return Err(RecordingWriterError::Unavailable);
            }
            state.state = RecordingWriterState::Finalizing;
            let recording_id = state
                .recording_id
                .ok_or(RecordingWriterError::Unavailable)?;
            state.sender.take();
            (recording_id, state.task.take())
        };
        if let Some(task) = task {
            if task.await.is_err() {
                self.fail_after_error(recording_id, "finalization_failed")
                    .await;
                return Err(RecordingWriterError::Unavailable);
            }
        }
        let (segment_count, sample_count, has_gaps, failed) = {
            let state = self.state.lock().expect("recording writer state lock");
            (
                state.persisted_segment_count,
                state.persisted_sample_count,
                state.has_gaps,
                state.state == RecordingWriterState::Failed,
            )
        };
        if failed {
            return Err(RecordingWriterError::Unavailable);
        }
        let duration_seconds = sample_count as f64 / RECORDING_SAMPLE_RATE_HZ;
        if self
            .backend
            .finalize(recording_id, duration_seconds, segment_count, has_gaps)
            .await
            .is_err()
        {
            self.fail_after_error(recording_id, "finalization_failed")
                .await;
            return Err(RecordingWriterError::Unavailable);
        }
        self.reset_idle();
        Ok(())
    }

    /// Abort plain-WAV uploads and best-effort persist a generic failure state.
    pub(crate) async fn abort(&self, failure_code: RecordingFailureCode) {
        let (recording_id, task) = self.take_for_abort();
        if let Some(task) = task {
            task.abort();
            let _ = task.await;
        }
        if let Some(recording_id) = recording_id {
            let _ = self.backend.fail(recording_id, failure_code.as_str()).await;
        }
    }

    fn fail_without_waiting(&self, recording_id: Option<Uuid>, failure_code: &'static str) {
        let (_, task) = self.take_for_abort();
        if let Some(task) = task {
            task.abort();
        }
        if let Some(recording_id) = recording_id {
            let backend = Arc::clone(&self.backend);
            tokio::spawn(async move {
                let _ = backend.fail(recording_id, failure_code).await;
            });
        }
    }

    async fn fail_after_error(&self, recording_id: Uuid, failure_code: &'static str) {
        self.mark_failed(None);
        let _ = self.backend.fail(recording_id, failure_code).await;
    }

    fn take_for_abort(&self) -> (Option<Uuid>, Option<JoinHandle<()>>) {
        let mut state = self.state.lock().expect("recording writer state lock");
        let recording_id = state.recording_id.take();
        let task = state.task.take();
        state.sender.take();
        state.expected_segment_index = 0;
        state.persisted_segment_count = 0;
        state.persisted_sample_count = 0;
        state.has_gaps = false;
        state.state = RecordingWriterState::Failed;
        (recording_id, task)
    }

    fn mark_failed(&self, recording_id: Option<Uuid>) {
        let mut state = self.state.lock().expect("recording writer state lock");
        state.sender.take();
        state.recording_id = recording_id;
        state.state = RecordingWriterState::Failed;
    }

    fn reset_idle(&self) {
        *self.state.lock().expect("recording writer state lock") = WriterState::default();
    }
}

impl<C: RecordingBackendClient> RecordingSegmentWriter for RecordingWriter<C> {
    fn try_enqueue(&self, segment: EncodedRecordingSegment) -> Result<(), RecordingWriterError> {
        Self::try_enqueue(self, segment)
    }

    fn set_has_gaps(&self, has_gaps: bool) {
        Self::set_has_gaps(self, has_gaps);
    }

    fn finalize<'a>(&'a self) -> RecordingFuture<'a, ()> {
        Box::pin(async move { Self::finalize(self).await })
    }

    fn abort<'a>(
        &'a self,
        failure_code: RecordingFailureCode,
    ) -> Pin<Box<dyn Future<Output = ()> + Send + 'a>> {
        Box::pin(async move { Self::abort(self, failure_code).await })
    }
}

async fn run_writer_task<C: RecordingBackendClient>(
    backend: Arc<C>,
    state: Arc<Mutex<WriterState>>,
    recording_id: Uuid,
    mut receiver: mpsc::Receiver<EncodedRecordingSegment>,
) {
    while let Some(segment) = receiver.recv().await {
        let sample_count = segment.sample_count;
        let expected_index = {
            state
                .lock()
                .expect("recording writer state lock")
                .expected_segment_index
        };
        if segment.segment_index != expected_index
            || backend.write_segment(recording_id, segment).await.is_err()
        {
            fail_worker(&backend, &state, recording_id).await;
            return;
        }
        let mut state = state.lock().expect("recording writer state lock");
        state.expected_segment_index = state.expected_segment_index.saturating_add(1);
        state.persisted_segment_count = state.persisted_segment_count.saturating_add(1);
        state.persisted_sample_count = state.persisted_sample_count.saturating_add(sample_count);
    }
}

async fn fail_worker<C: RecordingBackendClient>(
    backend: &Arc<C>,
    state: &Arc<Mutex<WriterState>>,
    recording_id: Uuid,
) {
    {
        let mut state = state.lock().expect("recording writer state lock");
        state.sender.take();
        state.recording_id = None;
        state.expected_segment_index = 0;
        state.persisted_segment_count = 0;
        state.persisted_sample_count = 0;
        state.has_gaps = false;
        state.state = RecordingWriterState::Failed;
    }
    let _ = backend.fail(recording_id, "writer_failed").await;
}

#[cfg(test)]
mod tests {
    use std::{
        sync::{Arc, Mutex},
        time::Duration,
    };

    use tokio::{
        sync::Notify,
        time::{sleep, timeout},
    };
    use uuid::Uuid;

    use super::{
        EncodedRecordingSegment, PrepareRecordingResponse, RecordingBackendClient,
        RecordingConfiguration, RecordingFailureCode, RecordingFuture, RecordingRetentionPolicy,
        RecordingWriter, RecordingWriterError,
    };

    #[derive(Default)]
    struct Calls {
        prepare: usize,
        start: usize,
        writes: Vec<u32>,
        finalized: Option<(f64, u32, bool)>,
        failures: Vec<String>,
    }

    struct FakeBackend {
        calls: Arc<Mutex<Calls>>,
        fail_prepare: bool,
        fail_start: bool,
        fail_write: bool,
        write_gate: Option<Arc<Notify>>,
    }

    impl FakeBackend {
        fn new() -> Self {
            Self {
                calls: Arc::new(Mutex::new(Calls::default())),
                fail_prepare: false,
                fail_start: false,
                fail_write: false,
                write_gate: None,
            }
        }
    }

    impl RecordingBackendClient for FakeBackend {
        fn prepare<'a>(
            &'a self,
            _meeting_id: Uuid,
            _configuration: &'a RecordingConfiguration,
        ) -> RecordingFuture<'a, PrepareRecordingResponse> {
            let calls = Arc::clone(&self.calls);
            let fail = self.fail_prepare;
            Box::pin(async move {
                calls.lock().expect("calls lock").prepare += 1;
                if fail {
                    return Err(RecordingWriterError::Unavailable);
                }
                Ok(PrepareRecordingResponse {
                    enabled: true,
                    recording_id: Some(Uuid::from_u128(2)),
                })
            })
        }

        fn start<'a>(&'a self, _recording_id: Uuid) -> RecordingFuture<'a, ()> {
            let calls = Arc::clone(&self.calls);
            let fail = self.fail_start;
            Box::pin(async move {
                calls.lock().expect("calls lock").start += 1;
                if fail {
                    Err(RecordingWriterError::Unavailable)
                } else {
                    Ok(())
                }
            })
        }

        fn write_segment<'a>(
            &'a self,
            _recording_id: Uuid,
            segment: EncodedRecordingSegment,
        ) -> RecordingFuture<'a, ()> {
            let calls = Arc::clone(&self.calls);
            let fail = self.fail_write;
            let gate = self.write_gate.clone();
            Box::pin(async move {
                calls
                    .lock()
                    .expect("calls lock")
                    .writes
                    .push(segment.segment_index);
                if let Some(gate) = gate {
                    gate.notified().await;
                }
                if fail {
                    Err(RecordingWriterError::Unavailable)
                } else {
                    Ok(())
                }
            })
        }

        fn finalize<'a>(
            &'a self,
            _recording_id: Uuid,
            duration_seconds: f64,
            segment_count: u32,
            has_gaps: bool,
        ) -> RecordingFuture<'a, ()> {
            let calls = Arc::clone(&self.calls);
            Box::pin(async move {
                calls.lock().expect("calls lock").finalized =
                    Some((duration_seconds, segment_count, has_gaps));
                Ok(())
            })
        }

        fn fail<'a>(
            &'a self,
            _recording_id: Uuid,
            failure_code: &'a str,
        ) -> RecordingFuture<'a, ()> {
            let calls = Arc::clone(&self.calls);
            Box::pin(async move {
                calls
                    .lock()
                    .expect("calls lock")
                    .failures
                    .push(failure_code.to_owned());
                Ok(())
            })
        }
    }

    fn configuration() -> RecordingConfiguration {
        RecordingConfiguration {
            enabled: true,
            consent_confirmed_at: Some("2026-01-01T00:00:00Z".to_owned()),
            retention_policy: RecordingRetentionPolicy::SevenDays,
        }
    }

    fn segment(index: u32, samples: usize) -> EncodedRecordingSegment {
        EncodedRecordingSegment::new(index, vec![index as u8, 1], samples).expect("segment")
    }

    async fn wait_for(condition: impl Fn() -> bool) {
        timeout(Duration::from_secs(1), async {
            while !condition() {
                sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("condition completes promptly");
    }

    #[tokio::test]
    async fn disabled_recording_never_contacts_the_backend() {
        let backend = Arc::new(FakeBackend::new());
        let writer = RecordingWriter::new(Arc::clone(&backend));

        assert!(!writer
            .prepare_and_start(Uuid::new_v4(), RecordingConfiguration::disabled())
            .await
            .expect("disabled"));
        assert_eq!(backend.calls.lock().expect("calls lock").prepare, 0);
    }

    #[tokio::test]
    async fn prepare_failure_isolated_before_any_writer_task_exists() {
        let mut fake = FakeBackend::new();
        fake.fail_prepare = true;
        let backend = Arc::new(fake);
        let writer = RecordingWriter::new(Arc::clone(&backend));

        assert_eq!(
            writer
                .prepare_and_start(Uuid::new_v4(), configuration())
                .await,
            Err(RecordingWriterError::Unavailable)
        );
        let calls = backend.calls.lock().expect("calls lock");
        assert_eq!(calls.prepare, 1);
        assert_eq!(calls.start, 0);
        assert!(calls.writes.is_empty());
    }

    #[tokio::test]
    async fn prepares_starts_and_writes_segments_strictly_in_order() {
        let backend = Arc::new(FakeBackend::new());
        let writer = RecordingWriter::new(Arc::clone(&backend));
        assert!(writer
            .prepare_and_start(Uuid::new_v4(), configuration())
            .await
            .expect("starts"));
        for index in 0..3 {
            writer
                .try_enqueue(segment(index, 16_000))
                .expect("enqueues");
            wait_for(|| {
                backend.calls.lock().expect("calls lock").writes.len() == (index + 1) as usize
            })
            .await;
        }
        writer.set_has_gaps(true);
        writer.finalize().await.expect("finalizes");

        let calls = backend.calls.lock().expect("calls lock");
        assert_eq!(calls.prepare, 1);
        assert_eq!(calls.start, 1);
        assert_eq!(calls.writes, vec![0, 1, 2]);
        assert_eq!(calls.finalized, Some((3.0, 3, true)));
    }

    #[tokio::test]
    async fn out_of_order_segment_fails_without_a_backend_write() {
        let backend = Arc::new(FakeBackend::new());
        let writer = RecordingWriter::new(Arc::clone(&backend));
        writer
            .prepare_and_start(Uuid::new_v4(), configuration())
            .await
            .expect("starts");
        writer
            .try_enqueue(segment(1, 1))
            .expect("accepted by queue");
        wait_for(|| {
            !backend
                .calls
                .lock()
                .expect("calls lock")
                .failures
                .is_empty()
        })
        .await;

        let calls = backend.calls.lock().expect("calls lock");
        assert!(calls.writes.is_empty());
        assert_eq!(calls.failures, vec!["writer_failed"]);
    }

    #[tokio::test]
    async fn full_queue_fails_recording_instead_of_dropping_a_segment() {
        let mut fake = FakeBackend::new();
        let gate = Arc::new(Notify::new());
        fake.write_gate = Some(Arc::clone(&gate));
        let backend = Arc::new(fake);
        let writer = RecordingWriter::new(Arc::clone(&backend));
        writer
            .prepare_and_start(Uuid::new_v4(), configuration())
            .await
            .expect("starts");
        writer.try_enqueue(segment(0, 1)).expect("first segment");
        wait_for(|| backend.calls.lock().expect("calls lock").writes == vec![0]).await;
        writer.try_enqueue(segment(1, 1)).expect("second segment");
        writer.try_enqueue(segment(2, 1)).expect("third segment");
        assert_eq!(
            writer.try_enqueue(segment(3, 1)),
            Err(RecordingWriterError::Unavailable)
        );
        gate.notify_waiters();
        wait_for(|| {
            !backend
                .calls
                .lock()
                .expect("calls lock")
                .failures
                .is_empty()
        })
        .await;
        assert_eq!(
            backend.calls.lock().expect("calls lock").failures,
            vec!["writer_failed"]
        );
    }

    #[tokio::test]
    async fn write_failure_isolated_and_finalization_never_reports_success() {
        let mut fake = FakeBackend::new();
        fake.fail_write = true;
        let backend = Arc::new(fake);
        let writer = RecordingWriter::new(Arc::clone(&backend));
        writer
            .prepare_and_start(Uuid::new_v4(), configuration())
            .await
            .expect("starts");
        writer.try_enqueue(segment(0, 1)).expect("enqueue");
        wait_for(|| {
            !backend
                .calls
                .lock()
                .expect("calls lock")
                .failures
                .is_empty()
        })
        .await;

        assert_eq!(
            writer.finalize().await,
            Err(RecordingWriterError::Unavailable)
        );
        assert!(backend
            .calls
            .lock()
            .expect("calls lock")
            .finalized
            .is_none());
    }

    #[tokio::test]
    async fn start_failure_isolated_and_abort_is_idempotent_and_redacted() {
        let mut fake = FakeBackend::new();
        fake.fail_start = true;
        let backend = Arc::new(fake);
        let writer = RecordingWriter::new(Arc::clone(&backend));
        assert_eq!(
            writer
                .prepare_and_start(Uuid::new_v4(), configuration())
                .await,
            Err(RecordingWriterError::Unavailable)
        );
        assert_eq!(
            backend.calls.lock().expect("calls lock").failures,
            vec!["capture_failed"]
        );

        writer.abort(RecordingFailureCode::Interrupted).await;
        writer.abort(RecordingFailureCode::Interrupted).await;
        let debug = format!("{:?}", segment(0, 1));
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("[0, 1]"));
    }

    #[tokio::test]
    async fn abort_cancels_an_active_writer_and_clears_plaintext_queue() {
        let mut fake = FakeBackend::new();
        let gate = Arc::new(Notify::new());
        fake.write_gate = Some(Arc::clone(&gate));
        let backend = Arc::new(fake);
        let writer = RecordingWriter::new(Arc::clone(&backend));
        writer
            .prepare_and_start(Uuid::new_v4(), configuration())
            .await
            .expect("starts");
        writer.try_enqueue(segment(0, 1)).expect("enqueue");
        wait_for(|| backend.calls.lock().expect("calls lock").writes == vec![0]).await;

        writer.abort(RecordingFailureCode::Interrupted).await;
        writer.abort(RecordingFailureCode::Interrupted).await;
        let state = writer.state.lock().expect("writer state lock");
        assert!(state.recording_id.is_none());
        assert!(state.sender.is_none());
        assert!(state.task.is_none());
        drop(state);
        assert_eq!(
            backend.calls.lock().expect("calls lock").failures,
            vec!["interrupted"]
        );
        gate.notify_waiters();
    }

    #[test]
    fn invalid_configuration_and_empty_segments_are_rejected() {
        assert!(RecordingConfiguration {
            enabled: true,
            consent_confirmed_at: None,
            retention_policy: RecordingRetentionPolicy::Manual,
        }
        .validate()
        .is_err());
        assert!(EncodedRecordingSegment::new(0, Vec::new(), 1).is_err());
        assert!(EncodedRecordingSegment::new(0, vec![1], 0).is_err());
    }
}
