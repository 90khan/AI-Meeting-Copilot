//! Lifecycle holder for native capture, DSP, chunk encoding, and submission.

use std::sync::Arc;

use thiserror::Error;
use tokio::sync::mpsc;

use super::{
    bridge::{NativeAudioCaptureBridge, NativeAudioFrameSender},
    chunker::{AudioChunk, AudioChunker},
    processing::AudioFrameProcessor,
    sender::{ChunkSenderTask, ChunkSubmitter, FinalizedChunkQueue},
    status::{AudioCaptureState, AudioCaptureStatus},
    types::{AudioCaptureConfiguration, NativeAudioFrame},
    wav::encode_audio_chunk,
};

const NATIVE_FRAME_BUFFER_CAPACITY: usize = 8;
const GAP_MESSAGE: &str = "An audio chunk was dropped because transcription fell behind.";
const SUBMISSION_FAILURE_MESSAGE: &str = "Audio submission failed.";

pub(crate) struct AudioCaptureCoordinator<B: NativeAudioCaptureBridge> {
    bridge: B,
    receiver: Option<mpsc::Receiver<NativeAudioFrame>>,
    processor: AudioFrameProcessor,
    chunker: AudioChunker,
    finalized_queue: Option<Arc<FinalizedChunkQueue>>,
    sender_task: Option<ChunkSenderTask>,
    submission_gap: bool,
    state: AudioCaptureState,
}

impl<B: NativeAudioCaptureBridge> AudioCaptureCoordinator<B> {
    pub(crate) fn new(bridge: B) -> Self {
        Self {
            bridge,
            receiver: None,
            processor: AudioFrameProcessor::default(),
            chunker: AudioChunker::default(),
            finalized_queue: None,
            sender_task: None,
            submission_gap: false,
            state: AudioCaptureState::Stopped,
        }
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
        {
            return Err(AudioCaptureCoordinatorError::AlreadyStarted);
        }

        self.state = AudioCaptureState::Starting;
        self.submission_gap = false;
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

        self.receiver = Some(receiver);
        self.finalized_queue = Some(finalized_queue);
        self.sender_task = Some(sender_task);
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
        let native_stop = self.bridge.stop();
        self.receiver = None;
        if let Some(queue) = self.finalized_queue.take() {
            queue.close();
            queue.clear();
        }
        if let Some(sender_task) = self.sender_task.take() {
            sender_task.stop().await;
        }
        self.processor.reset();
        self.chunker.reset();
        self.submission_gap = false;

        if native_stop.is_err() {
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StopFailed);
        }
        self.state = AudioCaptureState::Stopped;
        self.status()
    }

    pub(crate) fn try_receive_frame(&mut self) -> Option<NativeAudioFrame> {
        self.receiver.as_mut()?.try_recv().ok()
    }

    pub(crate) fn process_next_frame(
        &mut self,
    ) -> Result<Vec<super::mixer::MixedAudioFrame>, super::resampler::AudioProcessingError> {
        self.try_receive_frame()
            .map(|frame| self.processor.process(frame))
            .transpose()
            .map(Option::unwrap_or_default)
    }

    /// Processes one native frame and enqueues every complete encoded chunk.
    pub(crate) fn process_next_chunk(
        &mut self,
    ) -> Result<Vec<AudioChunk>, AudioCaptureCoordinatorError> {
        self.observe_sender_failure();
        if self.state != AudioCaptureState::Capturing {
            return Err(AudioCaptureCoordinatorError::NotCapturing);
        }

        let frames = self
            .process_next_frame()
            .map_err(|_| AudioCaptureCoordinatorError::ProcessingFailed)?;
        let mut chunks = Vec::new();
        for frame in frames {
            let result = self
                .chunker
                .push(frame)
                .map_err(|_| AudioCaptureCoordinatorError::ProcessingFailed)?;
            if result.gap_detected {
                self.submission_gap = true;
            }
            for chunk in result.chunks {
                let encoded = encode_audio_chunk(chunk.clone())
                    .map_err(|_| AudioCaptureCoordinatorError::EncodingFailed)?;
                let queue = self
                    .finalized_queue
                    .as_ref()
                    .ok_or(AudioCaptureCoordinatorError::SubmissionUnavailable)?;
                if queue.push(encoded) {
                    self.submission_gap = true;
                }
                chunks.push(chunk);
            }
        }
        self.observe_sender_failure();
        Ok(chunks)
    }

    pub(crate) fn status(&mut self) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        self.observe_sender_failure();
        let message = if self.state == AudioCaptureState::Failed {
            Some(SUBMISSION_FAILURE_MESSAGE.to_owned())
        } else if self.submission_gap
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
        if !self
            .sender_task
            .as_ref()
            .is_some_and(ChunkSenderTask::failed)
        {
            return;
        }
        let _ = self.bridge.stop();
        self.receiver = None;
        if let Some(queue) = &self.finalized_queue {
            queue.clear();
        }
        self.processor.reset();
        self.chunker.reset();
        self.state = AudioCaptureState::Failed;
    }
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
    use std::{future::Future, pin::Pin, sync::Arc};

    use super::{AudioCaptureCoordinator, AudioCaptureCoordinatorError};
    use crate::{
        audio_capture::{
            bridge::{
                NativeAudioCaptureBridge, NativeAudioCaptureBridgeError, NativeAudioFrameSender,
            },
            sender::ChunkSubmitter,
            status::{AudioCaptureState, AudioCaptureStatus},
            types::AudioCaptureConfiguration,
            wav::EncodedAudioChunk,
        },
        live_transcription::client::{ChunkSubmissionResult, LiveTranscriptionClientError},
    };

    struct FakeBridge;

    impl NativeAudioCaptureBridge for FakeBridge {
        fn start(
            &mut self,
            _configuration: &AudioCaptureConfiguration,
            _frame_sender: NativeAudioFrameSender,
        ) -> Result<(), NativeAudioCaptureBridgeError> {
            Ok(())
        }

        fn stop(&mut self) -> Result<(), NativeAudioCaptureBridgeError> {
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

    #[tokio::test]
    async fn coordinator_rejects_duplicate_start_and_stops_idempotently() {
        let mut coordinator = AudioCaptureCoordinator::new(FakeBridge);
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
}
