//! Privacy-safe capture lifecycle state for future desktop commands.

use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioCaptureState {
    Stopped,
    Starting,
    Capturing,
    Stopping,
    Failed,
}

/// Public status that excludes device identifiers, native errors, and audio data.
#[derive(Clone, PartialEq, Eq, Serialize)]
pub struct AudioCaptureStatus {
    state: AudioCaptureState,
    message: Option<String>,
}

impl AudioCaptureStatus {
    pub fn new(
        state: AudioCaptureState,
        message: Option<String>,
    ) -> Result<Self, AudioCaptureStatusError> {
        if message
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(AudioCaptureStatusError::BlankMessage);
        }
        Ok(Self { state, message })
    }

    pub const fn state(&self) -> AudioCaptureState {
        self.state
    }

    pub fn message(&self) -> Option<&str> {
        self.message.as_deref()
    }
}

impl std::fmt::Debug for AudioCaptureStatus {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AudioCaptureStatus")
            .field("state", &self.state)
            .field("message", &self.message.as_ref().map(|_| "[REDACTED]"))
            .finish()
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum AudioCaptureStatusError {
    #[error("The audio capture status message is invalid.")]
    BlankMessage,
}

#[cfg(test)]
mod tests {
    use super::{AudioCaptureState, AudioCaptureStatus, AudioCaptureStatusError};

    #[test]
    fn serializes_public_state_without_sensitive_fields() {
        let status = AudioCaptureStatus::new(
            AudioCaptureState::Capturing,
            Some("Audio capture is active.".to_owned()),
        )
        .expect("status is valid");
        let serialized = serde_json::to_string(&status).expect("status serializes");

        assert_eq!(
            serde_json::to_string(&AudioCaptureState::Capturing).expect("state serializes"),
            r#""capturing""#
        );
        assert!(!serialized.contains("token"));
        assert!(!format!("{status:?}").contains("Audio capture is active."));
    }

    #[test]
    fn rejects_blank_messages() {
        assert_eq!(
            AudioCaptureStatus::new(AudioCaptureState::Failed, Some(" ".to_owned())),
            Err(AudioCaptureStatusError::BlankMessage)
        );
    }
}
