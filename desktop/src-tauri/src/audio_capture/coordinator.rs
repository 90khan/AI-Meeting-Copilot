//! Lifecycle holder for native capture, DSP, chunk encoding, and submission.

use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};

use thiserror::Error;
use tokio::{
    sync::{mpsc, Notify},
    task::JoinHandle,
};

use super::{
    bridge::{NativeAudioCaptureBridge, NativeAudioFrameSender},
    chunker::AudioChunker,
    processing::AudioFrameProcessor,
    sender::{ChunkSenderTask, ChunkSubmitter, FinalizedChunkQueue},
    status::{AudioCaptureState, AudioCaptureStatus},
    types::{AudioCaptureConfiguration, NativeAudioFrame},
    wav::encode_audio_chunk,
};

const NATIVE_FRAME_BUFFER_CAPACITY: usize = 8;
const GAP_MESSAGE: &str = "An audio chunk was dropped because transcription fell behind.";
const SUBMISSION_FAILURE_MESSAGE: &str = "Audio submission failed.";

struct ProcessingPipeline {
    processor: AudioFrameProcessor,
    chunker: AudioChunker,
    submission_gap: bool,
}

impl Default for ProcessingPipeline {
    fn default() -> Self {
        Self {
            processor: AudioFrameProcessor::default(),
            chunker: AudioChunker::default(),
            submission_gap: false,
        }
    }
}

impl ProcessingPipeline {
    fn reset(&mut self) {
        self.processor.reset();
        self.chunker.reset();
        self.submission_gap = false;
    }
}

pub(crate) struct AudioCaptureCoordinator<B: NativeAudioCaptureBridge> {
    bridge: B,
    pipeline: Arc<Mutex<ProcessingPipeline>>,
    processing_task: Option<JoinHandle<()>>,
    processing_failed: Arc<AtomicBool>,
    stopping: Arc<AtomicBool>,
    finalized_queue: Option<Arc<FinalizedChunkQueue>>,
    sender_task: Option<ChunkSenderTask>,
    state: AudioCaptureState,
}

impl<B: NativeAudioCaptureBridge> AudioCaptureCoordinator<B> {
    pub(crate) fn new(bridge: B) -> Self {
        Self {
            bridge,
            pipeline: Arc::new(Mutex::new(ProcessingPipeline::default())),
            processing_task: None,
            processing_failed: Arc::new(AtomicBool::new(false)),
            stopping: Arc::new(AtomicBool::new(false)),
            finalized_queue: None,
            sender_task: None,
            state: AudioCaptureState::Stopped,
        }
    }

    /// Administrative operations are intentionally exposed only to the Rust
    /// command boundary; audio callbacks never use this access path.
    pub(crate) fn bridge_mut(&mut self) -> &mut B {
        &mut self.bridge
    }

    /// Starts capture only after a submission boundary is supplied.
    pub(crate) async fn start(
        &mut self,
        configuration: &AudioCaptureConfiguration,
        submitter: Arc<dyn ChunkSubmitter>,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if !matches!(
            self.state,
            AudioCaptureState::Stopped | AudioCaptureState::Failed
        ) || self.sender_task.is_some()
            || self.processing_task.is_some()
        {
            return Err(AudioCaptureCoordinatorError::AlreadyStarted);
        }

        self.state = AudioCaptureState::Starting;
        self.processing_failed.store(false, Ordering::Release);
        self.stopping.store(false, Ordering::Release);
        self.pipeline
            .lock()
            .expect("processing pipeline lock")
            .reset();
        let (sender_task, finalized_queue) = ChunkSenderTask::start(submitter);
        let (sender, receiver) = mpsc::channel(NATIVE_FRAME_BUFFER_CAPACITY);

        if self
            .bridge
            .start(configuration, NativeAudioFrameSender::new(sender))
            .is_err()
        {
            sender_task.stop().await;
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StartFailed);
        }

        let sender_failed = sender_task.failure_signal();
        let sender_failure_notify = sender_task.failure_notifier();
        let processing_task = tokio::spawn(run_processing_worker(
            receiver,
            Arc::clone(&self.pipeline),
            Arc::clone(&finalized_queue),
            Arc::clone(&self.processing_failed),
            Arc::clone(&self.stopping),
            sender_failed,
            sender_failure_notify,
        ));
        self.finalized_queue = Some(finalized_queue);
        self.sender_task = Some(sender_task);
        self.processing_task = Some(processing_task);
        self.state = AudioCaptureState::Capturing;
        self.status()
    }

    /// Stops native capture, joins the sender task, and discards queued WAV data.
    pub(crate) async fn stop(
        &mut self,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if self.state == AudioCaptureState::Stopped && self.sender_task.is_none() {
            return self.status();
        }

        self.state = AudioCaptureState::Stopping;
        self.stopping.store(true, Ordering::Release);
        let native_stop = self.bridge.stop();
        if let Some(task) = self.processing_task.take() {
            task.abort();
            let _ = task.await;
        }
        if let Some(queue) = self.finalized_queue.take() {
            queue.close();
            queue.clear();
        }
        if let Some(sender_task) = self.sender_task.take() {
            sender_task.stop().await;
        }
        self.pipeline
            .lock()
            .expect("processing pipeline lock")
            .reset();

        if native_stop.is_err() {
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StopFailed);
        }
        self.state = AudioCaptureState::Stopped;
        self.status()
    }

    pub(crate) fn status(&mut self) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        self.observe_sender_failure();
        let message = if self.state == AudioCaptureState::Failed {
            Some(SUBMISSION_FAILURE_MESSAGE.to_owned())
        } else if self
            .pipeline
            .lock()
            .expect("processing pipeline lock")
            .submission_gap
            || self
                .finalized_queue
                .as_ref()
                .is_some_and(|queue| queue.dropped())
        {
            Some(GAP_MESSAGE.to_owned())
        } else {
            None
        };
        AudioCaptureStatus::new(self.state, message)
            .map_err(|_| AudioCaptureCoordinatorError::StatusFailed)
    }

    fn observe_sender_failure(&mut self) {
        if !self.processing_failed.load(Ordering::Acquire)
            && !self
                .sender_task
                .as_ref()
                .is_some_and(ChunkSenderTask::failed)
        {
            return;
        }
        let _ = self.bridge.stop();
        if let Some(task) = self.processing_task.take() {
            task.abort();
        }
        if let Some(queue) = &self.finalized_queue {
            queue.clear();
        }
        self.pipeline
            .lock()
            .expect("processing pipeline lock")
            .reset();
        self.state = AudioCaptureState::Failed;
    }
}

async fn run_processing_worker(
    mut receiver: mpsc::Receiver<NativeAudioFrame>,
    pipeline: Arc<Mutex<ProcessingPipeline>>,
    finalized_queue: Arc<FinalizedChunkQueue>,
    processing_failed: Arc<AtomicBool>,
    stopping: Arc<AtomicBool>,
    sender_failed: Arc<AtomicBool>,
    sender_failure_notify: Arc<Notify>,
) {
    loop {
        if sender_failed.load(Ordering::Acquire) {
            finalized_queue.clear();
            processing_failed.store(true, Ordering::Release);
            return;
        }
        let native_frame = tokio::select! {
            frame = receiver.recv() => frame,
            () = sender_failure_notify.notified() => {
                finalized_queue.clear();
                processing_failed.store(true, Ordering::Release);
                return;
            }
        };
        let Some(native_frame) = native_frame else {
            if !stopping.load(Ordering::Acquire) {
                processing_failed.store(true, Ordering::Release);
            }
            return;
        };
        let result = process_native_frame(&pipeline, &finalized_queue, native_frame);
        if result.is_err() {
            finalized_queue.clear();
            processing_failed.store(true, Ordering::Release);
            return;
        }
    }
}

fn process_native_frame(
    pipeline: &Arc<Mutex<ProcessingPipeline>>,
    finalized_queue: &Arc<FinalizedChunkQueue>,
    native_frame: NativeAudioFrame,
) -> Result<(), AudioCaptureCoordinatorError> {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    let frames = pipeline
        .processor
        .process(native_frame)
        .map_err(|_| AudioCaptureCoordinatorError::ProcessingFailed)?;
    for frame in frames {
        let result = pipeline
            .chunker
            .push(frame)
            .map_err(|_| AudioCaptureCoordinatorError::ProcessingFailed)?;
        if result.gap_detected {
            pipeline.submission_gap = true;
        }
        for chunk in result.chunks {
            let encoded = encode_audio_chunk(chunk)
                .map_err(|_| AudioCaptureCoordinatorError::EncodingFailed)?;
            if finalized_queue.push(encoded) {
                pipeline.submission_gap = true;
            }
        }
    }
    Ok(())
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum AudioCaptureCoordinatorError {
    #[error("Audio capture is already starting or active.")]
    AlreadyStarted,
    #[error("Audio capture could not be started.")]
    StartFailed,
    #[error("Audio capture could not be stopped.")]
    StopFailed,
    #[error("Audio capture is not active.")]
    NotCapturing,
    #[error("Audio processing failed.")]
    ProcessingFailed,
    #[error("Audio encoding failed.")]
    EncodingFailed,
    #[error("Audio submission is unavailable.")]
    SubmissionUnavailable,
    #[error("Audio capture state is invalid.")]
    StatusFailed,
}

#[cfg(test)]
mod tests {
    use std::{
        future::Future,
        pin::Pin,
        sync::{Arc, Mutex},
        time::Duration,
    };

    use tokio::time::{sleep, timeout};

    use super::{AudioCaptureCoordinator, AudioCaptureCoordinatorError};
    use crate::{
        audio_capture::{
            bridge::{
                NativeAudioCaptureBridge, NativeAudioCaptureBridgeError, NativeAudioFrameSender,
            },
            sender::ChunkSubmitter,
            status::{AudioCaptureState, AudioCaptureStatus},
            types::{
                AudioCaptureConfiguration, NativeAudioFormat, NativeAudioFrame, NativeAudioSamples,
                NativeAudioSource, NativeSampleFormat,
            },
            wav::EncodedAudioChunk,
        },
        live_transcription::client::{ChunkSubmissionResult, LiveTranscriptionClientError},
    };

    struct FakeBridge {
        sender: Arc<Mutex<Option<NativeAudioFrameSender>>>,
        stop_calls: Arc<Mutex<usize>>,
    }

    impl NativeAudioCaptureBridge for FakeBridge {
        fn start(
            &mut self,
            _configuration: &AudioCaptureConfiguration,
            frame_sender: NativeAudioFrameSender,
        ) -> Result<(), NativeAudioCaptureBridgeError> {
            *self.sender.lock().expect("fake bridge lock") = Some(frame_sender);
            Ok(())
        }

        fn stop(&mut self) -> Result<(), NativeAudioCaptureBridgeError> {
            *self.stop_calls.lock().expect("fake bridge lock") += 1;
            self.sender.lock().expect("fake bridge lock").take();
            Ok(())
        }

        fn status(&self) -> AudioCaptureStatus {
            AudioCaptureStatus::new(AudioCaptureState::Stopped, None).expect("valid status")
        }
    }

    struct FakeSubmitter;

    impl ChunkSubmitter for FakeSubmitter {
        fn submit(
            &self,
            _chunk: EncodedAudioChunk,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            Box::pin(async {
                Ok(ChunkSubmissionResult {
                    sequence: 0,
                    skipped_silence: false,
                    accepted_segment_count: 0,
                    gap_reported: false,
                })
            })
        }
    }

    struct RecordingSubmitter {
        sequences: Arc<Mutex<Vec<u64>>>,
    }

    struct FailingSubmitter;

    impl ChunkSubmitter for FailingSubmitter {
        fn submit(
            &self,
            _chunk: EncodedAudioChunk,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            Box::pin(async { Err(LiveTranscriptionClientError::ConnectionFailed) })
        }
    }

    impl ChunkSubmitter for RecordingSubmitter {
        fn submit(
            &self,
            chunk: EncodedAudioChunk,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            Box::pin(async move {
                self.sequences
                    .lock()
                    .expect("recording submitter lock")
                    .push(chunk.sequence);
                Ok(ChunkSubmissionResult {
                    sequence: chunk.sequence,
                    skipped_silence: false,
                    accepted_segment_count: 0,
                    gap_reported: false,
                })
            })
        }
    }

    fn bridge() -> (FakeBridge, Arc<Mutex<Option<NativeAudioFrameSender>>>) {
        let sender = Arc::new(Mutex::new(None));
        (
            FakeBridge {
                sender: Arc::clone(&sender),
                stop_calls: Arc::new(Mutex::new(0)),
            },
            sender,
        )
    }

    fn native_frame(sample_count: usize) -> NativeAudioFrame {
        NativeAudioFrame::new(
            NativeAudioSource::SystemAudio,
            NativeAudioFormat::new(16_000, 1, NativeSampleFormat::Float32, true).expect("format"),
            0.0,
            NativeAudioSamples::Float32(vec![0.0; sample_count]),
        )
        .expect("frame")
    }

    #[tokio::test]
    async fn coordinator_rejects_duplicate_start_and_stops_idempotently() {
        let (bridge, _) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let configuration = AudioCaptureConfiguration::default();
        assert_eq!(
            coordinator
                .start(&configuration, Arc::new(FakeSubmitter))
                .await
                .expect("starts")
                .state(),
            AudioCaptureState::Capturing
        );
        assert_eq!(
            coordinator
                .start(&configuration, Arc::new(FakeSubmitter))
                .await,
            Err(AudioCaptureCoordinatorError::AlreadyStarted)
        );
        assert_eq!(
            coordinator.stop().await.expect("stops").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(
            coordinator.stop().await.expect("stops again").state(),
            AudioCaptureState::Stopped
        );
    }

    #[tokio::test]
    async fn worker_drains_native_frames_and_submits_finalized_wav_chunks() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        coordinator
            .start(
                &AudioCaptureConfiguration::default(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(32_001))
            .expect("callback stays non-blocking");

        timeout(Duration::from_secs(1), async {
            loop {
                if submitted
                    .lock()
                    .expect("recording submitter lock")
                    .as_slice()
                    == [0]
                {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("worker submits one finalized chunk");
        assert_eq!(
            coordinator.status().expect("status").state(),
            AudioCaptureState::Capturing
        );
        coordinator.stop().await.expect("stops");
    }

    #[tokio::test]
    async fn unexpected_native_channel_close_marks_capture_failed() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        coordinator
            .start(
                &AudioCaptureConfiguration::default(),
                Arc::new(FakeSubmitter),
            )
            .await
            .expect("starts");
        sender.lock().expect("fake bridge lock").take();

        timeout(Duration::from_secs(1), async {
            loop {
                if coordinator.status().expect("status").state() == AudioCaptureState::Failed {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("channel close is observed");
        coordinator.stop().await.expect("idempotent cleanup");
    }

    #[tokio::test]
    async fn submission_failure_ends_the_worker_without_exposing_audio() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        coordinator
            .start(
                &AudioCaptureConfiguration::default(),
                Arc::new(FailingSubmitter),
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(32_001))
            .expect("callback stays non-blocking");

        timeout(Duration::from_secs(1), async {
            loop {
                if coordinator.status().expect("status").state() == AudioCaptureState::Failed {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("failure is observed");
        let status = coordinator.status().expect("privacy-safe status");
        assert_eq!(status.message(), Some("Audio submission failed."));
        coordinator.stop().await.expect("stops failed capture");
    }
}
