//! Typed, privacy-safe desktop events emitted from backend Assist Mode messages.

use serde::Serialize;
use tauri::{AppHandle, Emitter};
use thiserror::Error;
use uuid::Uuid;

pub(crate) const TRANSCRIPT_SEGMENT_EVENT: &str = "assist://transcript-segment";
pub(crate) const SEGMENT_UPDATE_EVENT: &str = "assist://segment-update";
pub(crate) const REPLY_SUGGESTIONS_EVENT: &str = "assist://reply-suggestions";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct TranscriptSegmentEvent {
    pub(crate) transcript_id: Uuid,
    pub(crate) text: String,
    pub(crate) timestamp: String,
    pub(crate) source: String,
    pub(crate) speaker: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AssistSegmentCapability {
    Translation,
    Simplification,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AssistUpdateState {
    Processing,
    Ready,
    Failed,
    Unavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct AssistSegmentUpdateEvent {
    pub(crate) transcript_id: Uuid,
    pub(crate) capability: AssistSegmentCapability,
    pub(crate) state: AssistUpdateState,
    pub(crate) translated_text: Option<String>,
    pub(crate) simplified_text: Option<String>,
    pub(crate) target_level: Option<String>,
    pub(crate) message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct ReplySuggestionEvent {
    pub(crate) text: String,
    pub(crate) tone: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct AssistReplySuggestionsEvent {
    pub(crate) anchor_transcript_id: Uuid,
    pub(crate) state: AssistUpdateState,
    pub(crate) suggestions: Vec<ReplySuggestionEvent>,
    pub(crate) message: Option<String>,
}

#[derive(Debug, Error)]
#[error("Assist Mode event delivery failed.")]
pub(crate) struct AssistEventDeliveryError;

/// A narrow, fakeable boundary for typed desktop event delivery.
pub(crate) trait AssistEventSink: Send + Sync + 'static {
    fn emit_transcript_segment(
        &self,
        event: TranscriptSegmentEvent,
    ) -> Result<(), AssistEventDeliveryError>;
    fn emit_segment_update(
        &self,
        event: AssistSegmentUpdateEvent,
    ) -> Result<(), AssistEventDeliveryError>;
    fn emit_reply_suggestions(
        &self,
        event: AssistReplySuggestionsEvent,
    ) -> Result<(), AssistEventDeliveryError>;
}

/// Tauri-local event sink. Emission failures intentionally expose no detail.
pub(crate) struct TauriAssistEventSink {
    app_handle: AppHandle,
}

impl TauriAssistEventSink {
    pub(crate) fn new(app_handle: AppHandle) -> Self {
        Self { app_handle }
    }
}

impl AssistEventSink for TauriAssistEventSink {
    fn emit_transcript_segment(
        &self,
        event: TranscriptSegmentEvent,
    ) -> Result<(), AssistEventDeliveryError> {
        self.app_handle
            .emit(TRANSCRIPT_SEGMENT_EVENT, event)
            .map_err(|_| AssistEventDeliveryError)
    }

    fn emit_segment_update(
        &self,
        event: AssistSegmentUpdateEvent,
    ) -> Result<(), AssistEventDeliveryError> {
        #[cfg(debug_assertions)]
        let capability = match event.capability {
            AssistSegmentCapability::Translation => "translation",
            AssistSegmentCapability::Simplification => "simplification",
        };
        self.app_handle
            .emit(SEGMENT_UPDATE_EVENT, event)
            .map_err(|_| AssistEventDeliveryError)?;
        #[cfg(debug_assertions)]
        eprintln!("assist event emitted capability={capability}");
        Ok(())
    }

    fn emit_reply_suggestions(
        &self,
        event: AssistReplySuggestionsEvent,
    ) -> Result<(), AssistEventDeliveryError> {
        self.app_handle
            .emit(REPLY_SUGGESTIONS_EVENT, event)
            .map_err(|_| AssistEventDeliveryError)
    }
}
