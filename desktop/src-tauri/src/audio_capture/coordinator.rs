//! Lifecycle holder for one native capture bridge and its bounded frame queue.

use thiserror::Error;
use tokio::sync::mpsc;

use super::{
    bridge::{NativeAudioCaptureBridge, NativeAudioFrameSender},
    chunker::{AudioChunk, AudioChunker, AudioChunkerError},
    processing::AudioFrameProcessor,
    status::{AudioCaptureState, AudioCaptureStatus},
    types::{AudioCaptureConfiguration, NativeAudioFrame},
};

const NATIVE_FRAME_BUFFER_CAPACITY: usize = 8;

pub(crate) struct AudioCaptureCoordinator<B: NativeAudioCaptureBridge> {
    bridge: B,
    receiver: Option<mpsc::Receiver<NativeAudioFrame>>,
    processor: AudioFrameProcessor,
    chunker: AudioChunker,
    state: AudioCaptureState,
}

impl<B: NativeAudioCaptureBridge> AudioCaptureCoordinator<B> {
    pub(crate) fn new(bridge: B) -> Self {
        Self {
            bridge,
            receiver: None,
            processor: AudioFrameProcessor::default(),
            chunker: AudioChunker::default(),
            state: AudioCaptureState::Stopped,
        }
    }

    pub(crate) fn start(
        &mut self,
        configuration: &AudioCaptureConfiguration,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if !matches!(
            self.state,
            AudioCaptureState::Stopped | AudioCaptureState::Failed
        ) {
            return Err(AudioCaptureCoordinatorError::AlreadyStarted);
        }
        let (sender, receiver) = mpsc::channel(NATIVE_FRAME_BUFFER_CAPACITY);
        self.state = AudioCaptureState::Starting;
        self.bridge
            .start(configuration, NativeAudioFrameSender::new(sender))
            .map_err(|_| {
                self.state = AudioCaptureState::Failed;
                AudioCaptureCoordinatorError::StartFailed
            })?;
        self.receiver = Some(receiver);
        self.state = AudioCaptureState::Capturing;
        self.status()
    }

    pub(crate) fn stop(&mut self) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if self.state == AudioCaptureState::Stopped {
            return self.status();
        }
        self.state = AudioCaptureState::Stopping;
        self.bridge.stop().map_err(|_| {
            self.state = AudioCaptureState::Failed;
            AudioCaptureCoordinatorError::StopFailed
        })?;
        self.receiver = None;
        self.processor.reset();
        self.chunker.reset();
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

    pub(crate) fn process_next_chunk(&mut self) -> Result<Vec<AudioChunk>, AudioChunkerError> {
        let frames = self
            .process_next_frame()
            .map_err(|_| AudioChunkerError::InvalidTimeline)?;
        let mut chunks = Vec::new();
        for frame in frames {
            chunks.extend(self.chunker.push(frame)?.chunks);
        }
        Ok(chunks)
    }

    pub(crate) fn status(&self) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        AudioCaptureStatus::new(self.state, None)
            .map_err(|_| AudioCaptureCoordinatorError::StatusFailed)
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
    #[error("Audio capture state is invalid.")]
    StatusFailed,
}

#[cfg(test)]
mod tests {
    use super::{AudioCaptureCoordinator, AudioCaptureCoordinatorError};
    use crate::audio_capture::{
        bridge::{NativeAudioCaptureBridge, NativeAudioCaptureBridgeError, NativeAudioFrameSender},
        status::{AudioCaptureState, AudioCaptureStatus},
        types::AudioCaptureConfiguration,
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

    #[test]
    fn coordinator_rejects_duplicate_start_and_stops_idempotently() {
        let mut coordinator = AudioCaptureCoordinator::new(FakeBridge);
        let configuration = AudioCaptureConfiguration::default();
        assert_eq!(
            coordinator.start(&configuration).expect("starts").state(),
            AudioCaptureState::Capturing
        );
        assert_eq!(
            coordinator.start(&configuration),
            Err(AudioCaptureCoordinatorError::AlreadyStarted)
        );
        assert_eq!(
            coordinator.stop().expect("stops").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(
            coordinator.stop().expect("stops again").state(),
            AudioCaptureState::Stopped
        );
    }
}
