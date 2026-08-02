//! Replaceable synchronous bridge contract for future Swift capture callbacks.

use thiserror::Error;
use tokio::sync::mpsc;

use super::{
    status::{AudioCaptureState, AudioCaptureStatus},
    types::{AudioCaptureConfiguration, NativeAudioFrame},
};

/// Bounded, non-async delivery handle for a native callback thread.
#[derive(Clone)]
pub(crate) struct NativeAudioFrameSender {
    sender: mpsc::Sender<NativeAudioFrame>,
}

impl NativeAudioFrameSender {
    pub(crate) fn new(sender: mpsc::Sender<NativeAudioFrame>) -> Self {
        Self { sender }
    }

    /// Never await in a native audio callback; callers handle overload explicitly.
    pub(crate) fn try_send(
        &self,
        frame: NativeAudioFrame,
    ) -> Result<(), NativeAudioFrameSendError> {
        self.sender.try_send(frame).map_err(|error| match error {
            mpsc::error::TrySendError::Full(_) => NativeAudioFrameSendError::Full,
            mpsc::error::TrySendError::Closed(_) => NativeAudioFrameSendError::Closed,
        })
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum NativeAudioFrameSendError {
    #[error("The native audio frame buffer is full.")]
    Full,
    #[error("The native audio frame buffer is closed.")]
    Closed,
}

/// Synchronous API suitable for a Swift callback; it contains no Apple types.
pub(crate) trait NativeAudioCaptureBridge: Send {
    fn start(
        &mut self,
        configuration: &AudioCaptureConfiguration,
        frame_sender: NativeAudioFrameSender,
    ) -> Result<(), NativeAudioCaptureBridgeError>;

    fn stop(&mut self) -> Result<(), NativeAudioCaptureBridgeError>;

    fn status(&self) -> AudioCaptureStatus;
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum NativeAudioCaptureBridgeError {
    #[error("Native audio capture could not be started.")]
    StartFailed,
    #[error("Native audio capture could not be stopped.")]
    StopFailed,
}

/// Minimal lifecycle holder. It intentionally does not consume or process frames yet.
pub(crate) struct AudioCaptureCoordinator<B: NativeAudioCaptureBridge> {
    bridge: B,
    state: AudioCaptureState,
}

impl<B: NativeAudioCaptureBridge> AudioCaptureCoordinator<B> {
    pub(crate) fn new(bridge: B) -> Self {
        Self {
            bridge,
            state: AudioCaptureState::Stopped,
        }
    }

    pub(crate) fn start(
        &mut self,
        configuration: &AudioCaptureConfiguration,
        frame_sender: NativeAudioFrameSender,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if !matches!(
            self.state,
            AudioCaptureState::Stopped | AudioCaptureState::Failed
        ) {
            return Err(AudioCaptureCoordinatorError::AlreadyStarted);
        }
        self.state = AudioCaptureState::Starting;
        if self.bridge.start(configuration, frame_sender).is_err() {
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StartFailed);
        }
        self.state = AudioCaptureState::Capturing;
        self.status()
    }

    pub(crate) fn stop(&mut self) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if self.state == AudioCaptureState::Stopped {
            return self.status();
        }
        self.state = AudioCaptureState::Stopping;
        if self.bridge.stop().is_err() {
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StopFailed);
        }
        self.state = AudioCaptureState::Stopped;
        self.status()
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
    use super::{
        AudioCaptureCoordinator, AudioCaptureCoordinatorError, NativeAudioCaptureBridge,
        NativeAudioCaptureBridgeError, NativeAudioFrameSender,
    };
    use crate::audio_capture::{
        status::{AudioCaptureState, AudioCaptureStatus},
        types::AudioCaptureConfiguration,
    };
    use tokio::sync::mpsc;

    struct FakeBridge {
        starts: usize,
        stops: usize,
    }

    impl NativeAudioCaptureBridge for FakeBridge {
        fn start(
            &mut self,
            _configuration: &AudioCaptureConfiguration,
            _frame_sender: NativeAudioFrameSender,
        ) -> Result<(), NativeAudioCaptureBridgeError> {
            self.starts += 1;
            Ok(())
        }

        fn stop(&mut self) -> Result<(), NativeAudioCaptureBridgeError> {
            self.stops += 1;
            Ok(())
        }

        fn status(&self) -> AudioCaptureStatus {
            AudioCaptureStatus::new(AudioCaptureState::Stopped, None).expect("valid status")
        }
    }

    #[test]
    fn fake_bridge_obeys_capture_lifecycle_contract() {
        let mut coordinator = AudioCaptureCoordinator::new(FakeBridge {
            starts: 0,
            stops: 0,
        });
        let (sender, _receiver) = mpsc::channel(1);
        let sender = NativeAudioFrameSender::new(sender);
        let configuration = AudioCaptureConfiguration::default();

        assert_eq!(
            coordinator
                .start(&configuration, sender.clone())
                .expect("starts")
                .state(),
            AudioCaptureState::Capturing
        );
        assert_eq!(
            coordinator.start(&configuration, sender),
            Err(AudioCaptureCoordinatorError::AlreadyStarted)
        );
        assert_eq!(
            coordinator.stop().expect("stops").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(
            coordinator.stop().expect("stopping again is safe").state(),
            AudioCaptureState::Stopped
        );
    }
}
