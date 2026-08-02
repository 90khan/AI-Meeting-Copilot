//! Replaceable synchronous bridge contract for future Swift capture callbacks.

use thiserror::Error;
use tokio::sync::mpsc;

use super::{
    status::AudioCaptureStatus,
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
pub(crate) trait NativeAudioCaptureBridge {
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
