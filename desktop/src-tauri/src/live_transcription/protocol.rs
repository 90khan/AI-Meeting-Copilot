//! Strict Rust-local representation of the versioned live-transcription protocol.

#![allow(dead_code)] // Session/audio operations are intentionally not frontend commands yet.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use uuid::Uuid;

use crate::assist_mode::events::{
    AssistReplySuggestionsEvent, AssistSegmentCapability, AssistSegmentUpdateEvent,
    AssistUpdateState, ReplySuggestionEvent,
};

pub const PROTOCOL_VERSION: u8 = 1;
pub const AUDIO_FRAME_MAGIC: &[u8; 4] = b"AMCP";
pub const AUDIO_MESSAGE_KIND: u8 = 1;
pub const DEFAULT_MAX_IN_FLIGHT_CHUNKS: u8 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AudioSource {
    Mixed,
    Microphone,
    SystemAudio,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SimplificationLevel {
    B1,
    B2,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all(deserialize = "camelCase", serialize = "snake_case"))]
pub(crate) struct AssistModeConfiguration {
    pub(crate) enabled: bool,
    pub(crate) translation_enabled: bool,
    pub(crate) simplification_enabled: bool,
    pub(crate) simplification_level: Option<SimplificationLevel>,
    pub(crate) reply_coaching_enabled: bool,
}

impl AssistModeConfiguration {
    pub(crate) fn validate(&self) -> Result<(), ProtocolError> {
        if !self.enabled {
            return Ok(());
        }
        if self.simplification_enabled != self.simplification_level.is_some() {
            return Err(ProtocolError::InvalidMessage);
        }
        Ok(())
    }
}

impl AudioSource {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Mixed => "mixed",
            Self::Microphone => "microphone",
            Self::SystemAudio => "system_audio",
        }
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum ProtocolError {
    #[error("The live-transcription protocol message is invalid.")]
    InvalidMessage,
    #[error("The live-transcription protocol version is unsupported.")]
    UnsupportedVersion,
    #[error("The live-transcription protocol limit is unsupported.")]
    UnsupportedLimit,
}

#[derive(Serialize)]
struct HelloMessage<'a> {
    #[serde(rename = "type")]
    message_type: &'static str,
    version: u8,
    token: &'a str,
    client_id: Uuid,
}

#[derive(Serialize)]
struct StartSessionMessage<'a> {
    #[serde(rename = "type")]
    message_type: &'static str,
    version: u8,
    request_id: Uuid,
    meeting_id: Uuid,
    language_hint: Option<&'a str>,
    source: AudioSource,
    assist_mode: &'a AssistModeConfiguration,
}

#[derive(Serialize)]
struct EndSessionMessage {
    #[serde(rename = "type")]
    message_type: &'static str,
    version: u8,
    request_id: Uuid,
    session_id: Uuid,
    last_sequence: u64,
}

/// Serialize the secret-bearing hello only at the transport boundary.
pub(crate) fn serialize_hello(token: &str, client_id: Uuid) -> Result<String, ProtocolError> {
    if token.trim().is_empty() {
        return Err(ProtocolError::InvalidMessage);
    }
    serde_json::to_string(&HelloMessage {
        message_type: "hello",
        version: PROTOCOL_VERSION,
        token,
        client_id,
    })
    .map_err(|_| ProtocolError::InvalidMessage)
}

pub(crate) fn serialize_start_session(
    request_id: Uuid,
    meeting_id: Uuid,
    language_hint: Option<&str>,
    source: AudioSource,
    assist_mode: &AssistModeConfiguration,
) -> Result<String, ProtocolError> {
    if language_hint.is_some_and(|value| value.trim().is_empty()) || assist_mode.validate().is_err()
    {
        return Err(ProtocolError::InvalidMessage);
    }
    serde_json::to_string(&StartSessionMessage {
        message_type: "start_session",
        version: PROTOCOL_VERSION,
        request_id,
        meeting_id,
        language_hint,
        source,
        assist_mode,
    })
    .map_err(|_| ProtocolError::InvalidMessage)
}

pub(crate) fn serialize_end_session(
    request_id: Uuid,
    session_id: Uuid,
    last_sequence: u64,
) -> Result<String, ProtocolError> {
    serde_json::to_string(&EndSessionMessage {
        message_type: "end_session",
        version: PROTOCOL_VERSION,
        request_id,
        session_id,
        last_sequence,
    })
    .map_err(|_| ProtocolError::InvalidMessage)
}

pub(crate) enum ServerMessage {
    HelloAck(HelloAck),
    SessionStarted(SessionStarted),
    ChunkResult {
        chunk_sequence: u64,
        response: ChunkResponse,
    },
    AssistSegmentUpdate(AssistSegmentUpdateEvent),
    AssistReplySuggestions(AssistReplySuggestionsEvent),
    Status,
    Error(ProtocolFailure),
    SessionStopped(SessionStopped),
}

/// Internal finalized transcript data retained for the future desktop event router.
#[derive(Clone)]
pub(crate) struct ChunkResultSegment {
    pub(crate) transcript_id: Uuid,
    pub(crate) text: String,
    pub(crate) timestamp: String,
    pub(crate) source: AudioSource,
    pub(crate) speaker: String,
}

#[derive(Clone)]
pub(crate) struct ChunkResponse {
    pub(crate) skipped_silence: bool,
    pub(crate) accepted_segments: Vec<ChunkResultSegment>,
}

pub(crate) struct HelloAck {
    pub(crate) connection_id: Uuid,
    pub(crate) max_binary_payload_bytes: usize,
}

pub(crate) struct SessionStarted {
    pub(crate) request_id: Uuid,
    pub(crate) session_id: Uuid,
    pub(crate) expected_sequence: u64,
}

pub(crate) struct ProtocolFailure {
    pub(crate) fatal: bool,
    pub(crate) expected_sequence: Option<u64>,
    pub(crate) session_start_stage: Option<SessionStartFailureStage>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SessionStartFailureStage {
    MeetingValidation,
    Factory,
    AssistInitialization,
    StartedSend,
}

impl SessionStartFailureStage {
    pub(crate) const fn identifier(self) -> &'static str {
        match self {
            Self::MeetingValidation => "session_meeting_validation",
            Self::Factory => "session_factory",
            Self::AssistInitialization => "session_assist_initialization",
            Self::StartedSend => "session_started_send",
        }
    }
}

pub(crate) struct SessionStopped {
    pub(crate) request_id: Uuid,
    pub(crate) session_id: Uuid,
}

/// Parse one strict server control message while discarding transcript content.
pub(crate) fn parse_server_message(input: &str) -> Result<ServerMessage, ProtocolError> {
    let value: Value = serde_json::from_str(input).map_err(|_| ProtocolError::InvalidMessage)?;
    let object = value.as_object().ok_or(ProtocolError::InvalidMessage)?;
    let message_type = required_string(object, "type")?;

    match message_type {
        "hello_ack" => parse_hello_ack(object),
        "session_started" => parse_session_started(object),
        "chunk_result" => parse_chunk_result(object),
        "assist_segment_update" => parse_assist_segment_update(object),
        "assist_reply_suggestions" => parse_assist_reply_suggestions(object),
        "status" => parse_status(object),
        "error" => parse_error(object),
        "session_stopped" => parse_session_stopped(object),
        _ => Err(ProtocolError::InvalidMessage),
    }
}

fn parse_assist_segment_update(
    object: &serde_json::Map<String, Value>,
) -> Result<ServerMessage, ProtocolError> {
    validate_version(object)?;
    let capability = match required_string(object, "capability")? {
        "translation" => AssistSegmentCapability::Translation,
        "simplification" => AssistSegmentCapability::Simplification,
        _ => return Err(ProtocolError::InvalidMessage),
    };
    let state = parse_assist_state(required_string(object, "state")?)?;
    let common = ["type", "version", "transcript_id", "capability", "state"];
    let mut expected: Vec<&str> = match (capability, state) {
        (AssistSegmentCapability::Translation, AssistUpdateState::Ready) => {
            [common.as_slice(), &["translated_text"]].concat()
        }
        (AssistSegmentCapability::Simplification, AssistUpdateState::Ready) => {
            [common.as_slice(), &["simplified_text", "target_level"]].concat()
        }
        _ => common.to_vec(),
    };
    let message = if state != AssistUpdateState::Ready && object.contains_key("message") {
        expected.push("message");
        Some(non_blank_string(object, "message")?.to_owned())
    } else {
        None
    };
    require_fields(object, &expected)?;

    let translated_text = if capability == AssistSegmentCapability::Translation
        && state == AssistUpdateState::Ready
    {
        Some(non_blank_string(object, "translated_text")?.to_owned())
    } else {
        None
    };
    let (simplified_text, target_level) = if capability == AssistSegmentCapability::Simplification
        && state == AssistUpdateState::Ready
    {
        let level = non_blank_string(object, "target_level")?;
        if !matches!(level, "b1" | "b2") {
            return Err(ProtocolError::InvalidMessage);
        }
        (
            Some(non_blank_string(object, "simplified_text")?.to_owned()),
            Some(level.to_owned()),
        )
    } else {
        (None, None)
    };
    Ok(ServerMessage::AssistSegmentUpdate(
        AssistSegmentUpdateEvent {
            transcript_id: parse_uuid(object, "transcript_id")?,
            capability,
            state,
            translated_text,
            simplified_text,
            target_level,
            message,
        },
    ))
}

fn parse_assist_reply_suggestions(
    object: &serde_json::Map<String, Value>,
) -> Result<ServerMessage, ProtocolError> {
    validate_version(object)?;
    let state = parse_assist_state(required_string(object, "state")?)?;
    let common = ["type", "version", "anchor_transcript_id", "state"];
    let mut expected: Vec<&str> = if state == AssistUpdateState::Ready {
        [common.as_slice(), &["suggestions"]].concat()
    } else {
        common.to_vec()
    };
    let message = if state != AssistUpdateState::Ready && object.contains_key("message") {
        expected.push("message");
        Some(non_blank_string(object, "message")?.to_owned())
    } else {
        None
    };
    require_fields(object, &expected)?;
    let suggestions = if state == AssistUpdateState::Ready {
        let values = object
            .get("suggestions")
            .and_then(Value::as_array)
            .ok_or(ProtocolError::InvalidMessage)?;
        if !(1..=2).contains(&values.len()) {
            return Err(ProtocolError::InvalidMessage);
        }
        values
            .iter()
            .map(parse_reply_suggestion)
            .collect::<Result<Vec<_>, _>>()?
    } else {
        Vec::new()
    };
    Ok(ServerMessage::AssistReplySuggestions(
        AssistReplySuggestionsEvent {
            anchor_transcript_id: parse_uuid(object, "anchor_transcript_id")?,
            state,
            suggestions,
            message,
        },
    ))
}

fn parse_assist_state(value: &str) -> Result<AssistUpdateState, ProtocolError> {
    match value {
        "processing" => Ok(AssistUpdateState::Processing),
        "ready" => Ok(AssistUpdateState::Ready),
        "failed" => Ok(AssistUpdateState::Failed),
        "unavailable" => Ok(AssistUpdateState::Unavailable),
        _ => Err(ProtocolError::InvalidMessage),
    }
}

fn parse_reply_suggestion(value: &Value) -> Result<ReplySuggestionEvent, ProtocolError> {
    let object = value.as_object().ok_or(ProtocolError::InvalidMessage)?;
    require_fields(object, &["text", "tone"])?;
    let tone = non_blank_string(object, "tone")?;
    if !matches!(tone, "neutral" | "professional" | "friendly" | "confident") {
        return Err(ProtocolError::InvalidMessage);
    }
    Ok(ReplySuggestionEvent {
        text: non_blank_string(object, "text")?.to_owned(),
        tone: tone.to_owned(),
    })
}

fn parse_hello_ack(
    object: &serde_json::Map<String, Value>,
) -> Result<ServerMessage, ProtocolError> {
    require_fields(
        object,
        &[
            "type",
            "version",
            "connection_id",
            "max_binary_payload_bytes",
            "max_in_flight_chunks",
        ],
    )?;
    validate_version(object)?;
    let max_in_flight = required_u64(object, "max_in_flight_chunks")?;
    if max_in_flight != u64::from(DEFAULT_MAX_IN_FLIGHT_CHUNKS) {
        return Err(ProtocolError::UnsupportedLimit);
    }
    let max_binary_payload_bytes =
        usize::try_from(required_u64(object, "max_binary_payload_bytes")?)
            .map_err(|_| ProtocolError::InvalidMessage)?;
    if max_binary_payload_bytes == 0 {
        return Err(ProtocolError::InvalidMessage);
    }
    Ok(ServerMessage::HelloAck(HelloAck {
        connection_id: parse_uuid(object, "connection_id")?,
        max_binary_payload_bytes,
    }))
}

fn parse_session_started(
    object: &serde_json::Map<String, Value>,
) -> Result<ServerMessage, ProtocolError> {
    require_fields(
        object,
        &[
            "type",
            "version",
            "request_id",
            "session_id",
            "expected_sequence",
        ],
    )?;
    validate_version(object)?;
    Ok(ServerMessage::SessionStarted(SessionStarted {
        request_id: parse_uuid(object, "request_id")?,
        session_id: parse_uuid(object, "session_id")?,
        expected_sequence: required_u64(object, "expected_sequence")?,
    }))
}

fn parse_chunk_result(
    object: &serde_json::Map<String, Value>,
) -> Result<ServerMessage, ProtocolError> {
    require_fields(
        object,
        &[
            "type",
            "version",
            "chunk_sequence",
            "accepted_segments",
            "skipped_silence",
        ],
    )?;
    validate_version(object)?;
    if !object.get("skipped_silence").is_some_and(Value::is_boolean) {
        return Err(ProtocolError::InvalidMessage);
    }
    let segments = object
        .get("accepted_segments")
        .and_then(Value::as_array)
        .ok_or(ProtocolError::InvalidMessage)?;
    let mut accepted_segments = Vec::with_capacity(segments.len());
    for segment in segments {
        let segment = segment.as_object().ok_or(ProtocolError::InvalidMessage)?;
        require_fields(
            segment,
            &["transcript_id", "text", "timestamp", "source", "speaker"],
        )?;
        let source = match required_string(segment, "source")? {
            "mixed" => AudioSource::Mixed,
            "microphone" => AudioSource::Microphone,
            "system_audio" => AudioSource::SystemAudio,
            _ => return Err(ProtocolError::InvalidMessage),
        };
        let text = required_string(segment, "text")?;
        let timestamp = required_string(segment, "timestamp")?;
        let speaker = required_string(segment, "speaker")?;
        if text.trim().is_empty() || timestamp.trim().is_empty() || speaker.trim().is_empty() {
            return Err(ProtocolError::InvalidMessage);
        }
        accepted_segments.push(ChunkResultSegment {
            transcript_id: parse_uuid(segment, "transcript_id")?,
            text: text.to_owned(),
            timestamp: timestamp.to_owned(),
            source,
            speaker: speaker.to_owned(),
        });
    }
    Ok(ServerMessage::ChunkResult {
        chunk_sequence: required_u64(object, "chunk_sequence")?,
        response: ChunkResponse {
            skipped_silence: object
                .get("skipped_silence")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            accepted_segments,
        },
    })
}

fn parse_status(object: &serde_json::Map<String, Value>) -> Result<ServerMessage, ProtocolError> {
    require_fields(
        object,
        &["type", "version", "kind", "message", "chunk_sequence"],
    )?;
    validate_version(object)?;
    if required_string(object, "kind")?.trim().is_empty()
        || !optional_string(object, "message")?.is_none_or(|message| message.trim().is_empty())
        || !matches!(
            required_string(object, "kind")?,
            "capture_active"
                | "processing"
                | "delayed"
                | "gap"
                | "permission_error"
                | "device_error"
                | "provider_error"
                | "stopping"
                | "stopped"
        )
    {
        return Err(ProtocolError::InvalidMessage);
    }
    let _ = optional_u64(object, "chunk_sequence")?;
    Ok(ServerMessage::Status)
}

fn parse_error(object: &serde_json::Map<String, Value>) -> Result<ServerMessage, ProtocolError> {
    require_fields(
        object,
        &[
            "type",
            "version",
            "code",
            "message",
            "fatal",
            "session_id",
            "request_id",
            "expected_sequence",
            "session_start_stage",
        ],
    )?;
    validate_version(object)?;
    if required_string(object, "code")?.trim().is_empty()
        || required_string(object, "message")?.trim().is_empty()
        || !object.get("fatal").is_some_and(Value::is_boolean)
    {
        return Err(ProtocolError::InvalidMessage);
    }
    optional_uuid(object, "session_id")?;
    optional_uuid(object, "request_id")?;
    Ok(ServerMessage::Error(ProtocolFailure {
        fatal: object
            .get("fatal")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        expected_sequence: optional_u64(object, "expected_sequence")?,
        session_start_stage: parse_optional_session_start_failure_stage(
            object,
            "session_start_stage",
        )?,
    }))
}

fn parse_optional_session_start_failure_stage(
    object: &serde_json::Map<String, Value>,
    field: &str,
) -> Result<Option<SessionStartFailureStage>, ProtocolError> {
    let Some(value) = object.get(field) else {
        return Err(ProtocolError::InvalidMessage);
    };
    if value.is_null() {
        return Ok(None);
    }
    match value.as_str() {
        Some("session_meeting_validation") => Ok(Some(SessionStartFailureStage::MeetingValidation)),
        Some("session_factory") => Ok(Some(SessionStartFailureStage::Factory)),
        Some("session_assist_initialization") => {
            Ok(Some(SessionStartFailureStage::AssistInitialization))
        }
        Some("session_started_send") => Ok(Some(SessionStartFailureStage::StartedSend)),
        _ => Err(ProtocolError::InvalidMessage),
    }
}

fn parse_session_stopped(
    object: &serde_json::Map<String, Value>,
) -> Result<ServerMessage, ProtocolError> {
    require_fields(object, &["type", "version", "request_id", "session_id"])?;
    validate_version(object)?;
    Ok(ServerMessage::SessionStopped(SessionStopped {
        request_id: parse_uuid(object, "request_id")?,
        session_id: parse_uuid(object, "session_id")?,
    }))
}

fn require_fields(
    object: &serde_json::Map<String, Value>,
    expected: &[&str],
) -> Result<(), ProtocolError> {
    if object.len() != expected.len() || expected.iter().any(|field| !object.contains_key(*field)) {
        return Err(ProtocolError::InvalidMessage);
    }
    Ok(())
}

fn validate_version(object: &serde_json::Map<String, Value>) -> Result<(), ProtocolError> {
    if required_u64(object, "version")? != u64::from(PROTOCOL_VERSION) {
        return Err(ProtocolError::UnsupportedVersion);
    }
    Ok(())
}

fn required_string<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<&'a str, ProtocolError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or(ProtocolError::InvalidMessage)
}

fn non_blank_string<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<&'a str, ProtocolError> {
    let value = required_string(object, field)?;
    if value.trim().is_empty() {
        return Err(ProtocolError::InvalidMessage);
    }
    Ok(value)
}

fn optional_string<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<Option<&'a str>, ProtocolError> {
    match object.get(field) {
        Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value)),
        _ => Err(ProtocolError::InvalidMessage),
    }
}

fn required_u64(
    object: &serde_json::Map<String, Value>,
    field: &str,
) -> Result<u64, ProtocolError> {
    object
        .get(field)
        .and_then(Value::as_u64)
        .ok_or(ProtocolError::InvalidMessage)
}

fn optional_u64(
    object: &serde_json::Map<String, Value>,
    field: &str,
) -> Result<Option<u64>, ProtocolError> {
    match object.get(field) {
        Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_u64()
            .map(Some)
            .ok_or(ProtocolError::InvalidMessage),
        None => Err(ProtocolError::InvalidMessage),
    }
}

fn parse_uuid(object: &serde_json::Map<String, Value>, field: &str) -> Result<Uuid, ProtocolError> {
    required_string(object, field)?
        .parse()
        .map_err(|_| ProtocolError::InvalidMessage)
}

fn optional_uuid(
    object: &serde_json::Map<String, Value>,
    field: &str,
) -> Result<(), ProtocolError> {
    match object.get(field) {
        Some(Value::Null) => Ok(()),
        Some(Value::String(value)) if value.parse::<Uuid>().is_ok() => Ok(()),
        _ => Err(ProtocolError::InvalidMessage),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        parse_server_message, serialize_hello, serialize_start_session, AssistModeConfiguration,
        AudioSource, ProtocolError, ServerMessage, SimplificationLevel,
    };
    use uuid::Uuid;

    #[test]
    fn deserializes_tauri_assist_configuration_in_camel_case() {
        let configuration: AssistModeConfiguration = serde_json::from_str(
            r#"{"enabled":false,"translationEnabled":false,"simplificationEnabled":false,"simplificationLevel":null,"replyCoachingEnabled":false}"#,
        )
        .expect("Tauri input deserializes");

        assert_eq!(
            configuration,
            AssistModeConfiguration {
                enabled: false,
                translation_enabled: false,
                simplification_enabled: false,
                simplification_level: None,
                reply_coaching_enabled: false,
            }
        );
    }

    #[test]
    fn serializes_the_required_hello_shape() {
        let token = "token-value";
        let payload = serialize_hello(token, Uuid::nil()).expect("hello serializes");

        assert_eq!(
            payload,
            r#"{"type":"hello","version":1,"token":"token-value","client_id":"00000000-0000-0000-0000-000000000000"}"#
        );
    }

    #[test]
    fn validates_hello_ack_limits() {
        let valid = r#"{"type":"hello_ack","version":1,"connection_id":"00000000-0000-0000-0000-000000000000","max_binary_payload_bytes":10,"max_in_flight_chunks":1}"#;
        assert!(matches!(
            parse_server_message(valid),
            Ok(ServerMessage::HelloAck(_))
        ));

        let unsupported = valid.replace("\"max_in_flight_chunks\":1", "\"max_in_flight_chunks\":2");
        assert!(matches!(
            parse_server_message(&unsupported),
            Err(ProtocolError::UnsupportedLimit)
        ));
    }

    #[test]
    fn serializes_a_validated_assist_mode_configuration() {
        let configuration = AssistModeConfiguration {
            enabled: true,
            translation_enabled: true,
            simplification_enabled: true,
            simplification_level: Some(SimplificationLevel::B2),
            reply_coaching_enabled: true,
        };
        let payload = serialize_start_session(
            Uuid::nil(),
            Uuid::nil(),
            Some("de"),
            AudioSource::Mixed,
            &configuration,
        )
        .expect("configuration serializes");

        assert!(payload.contains(r#""assist_mode":{"enabled":true,"translation_enabled":true,"simplification_enabled":true,"simplification_level":"b2","reply_coaching_enabled":true}"#));
        assert!(!payload.contains("token"));
    }

    #[test]
    fn rejects_invalid_simplification_configuration() {
        let configuration = AssistModeConfiguration {
            enabled: true,
            translation_enabled: true,
            simplification_enabled: true,
            simplification_level: None,
            reply_coaching_enabled: false,
        };
        assert!(matches!(
            serialize_start_session(
                Uuid::nil(),
                Uuid::nil(),
                None,
                AudioSource::Mixed,
                &configuration,
            ),
            Err(ProtocolError::InvalidMessage)
        ));
    }

    #[test]
    fn serializes_disabled_assist_mode_with_a_null_level() {
        let configuration = AssistModeConfiguration {
            enabled: false,
            translation_enabled: false,
            simplification_enabled: false,
            simplification_level: None,
            reply_coaching_enabled: false,
        };
        let payload = serialize_start_session(
            Uuid::nil(),
            Uuid::nil(),
            None,
            AudioSource::Mixed,
            &configuration,
        )
        .expect("disabled configuration serializes");

        assert!(payload.contains(r#""assist_mode":{"enabled":false,"translation_enabled":false,"simplification_enabled":false,"simplification_level":null,"reply_coaching_enabled":false}"#));
    }

    #[test]
    fn parses_session_lifecycle_messages() {
        let request_id = "00000000-0000-0000-0000-000000000001";
        let session_id = "00000000-0000-0000-0000-000000000002";
        let started = format!(
            r#"{{"type":"session_started","version":1,"request_id":"{request_id}","session_id":"{session_id}","expected_sequence":0}}"#
        );
        let stopped = format!(
            r#"{{"type":"session_stopped","version":1,"request_id":"{request_id}","session_id":"{session_id}"}}"#
        );

        assert!(matches!(
            parse_server_message(&started),
            Ok(ServerMessage::SessionStarted(_))
        ));
        assert!(matches!(
            parse_server_message(&stopped),
            Ok(ServerMessage::SessionStopped(_))
        ));
    }

    #[test]
    fn rejects_malformed_server_messages() {
        assert!(matches!(
            parse_server_message("{}"),
            Err(ProtocolError::InvalidMessage)
        ));
        assert!(matches!(
            parse_server_message(r#"{"type":"hello_ack","version":2}"#),
            Err(ProtocolError::InvalidMessage)
        ));
        assert_eq!(AudioSource::SystemAudio.as_str(), "system_audio");
    }

    #[test]
    fn parses_only_allowlisted_session_start_failure_stages_without_error_details() {
        let payload = r#"{"type":"error","version":1,"code":"session_start_failed","message":"private token and provider detail","fatal":true,"session_id":null,"request_id":"00000000-0000-0000-0000-000000000001","expected_sequence":null,"session_start_stage":"session_factory"}"#;

        let ServerMessage::Error(error) = parse_server_message(payload).expect("error parses")
        else {
            panic!("expected protocol error");
        };

        assert_eq!(
            error
                .session_start_stage
                .expect("stage is retained")
                .identifier(),
            "session_factory"
        );
        assert!(matches!(
            parse_server_message(&payload.replace("session_factory", "private token")),
            Err(ProtocolError::InvalidMessage)
        ));
    }

    #[test]
    fn parses_chunk_result_segments_with_stable_transcript_ids_in_order() {
        let first_id = "00000000-0000-0000-0000-000000000010";
        let second_id = "00000000-0000-0000-0000-000000000011";
        let payload = format!(
            r#"{{"type":"chunk_result","version":1,"chunk_sequence":4,"accepted_segments":[{{"transcript_id":"{first_id}","text":"First transcript","timestamp":"2026-08-02T10:00:00Z","source":"mixed","speaker":"Unknown"}},{{"transcript_id":"{second_id}","text":"Second transcript","timestamp":"2026-08-02T10:00:01Z","source":"microphone","speaker":"Mira"}}],"skipped_silence":false}}"#
        );

        let ServerMessage::ChunkResult {
            chunk_sequence,
            response,
        } = parse_server_message(&payload).expect("chunk result parses")
        else {
            panic!("expected chunk result");
        };
        assert_eq!(chunk_sequence, 4);
        assert!(!response.skipped_silence);
        assert_eq!(response.accepted_segments.len(), 2);
        assert_eq!(
            response.accepted_segments[0].transcript_id.to_string(),
            first_id
        );
        assert_eq!(
            response.accepted_segments[1].transcript_id.to_string(),
            second_id
        );
        assert_eq!(response.accepted_segments[0].text, "First transcript");
        assert_eq!(
            response.accepted_segments[1].timestamp,
            "2026-08-02T10:00:01Z"
        );
        assert_eq!(
            response.accepted_segments[1].source,
            AudioSource::Microphone
        );
        assert_eq!(response.accepted_segments[1].speaker, "Mira");
    }

    #[test]
    fn rejects_missing_or_malformed_chunk_result_transcript_ids_without_leaking_text() {
        let missing = r#"{"type":"chunk_result","version":1,"chunk_sequence":0,"accepted_segments":[{"text":"private transcript","timestamp":"2026-08-02T10:00:00Z","source":"mixed","speaker":"Unknown"}],"skipped_silence":false}"#;
        let malformed = r#"{"type":"chunk_result","version":1,"chunk_sequence":0,"accepted_segments":[{"transcript_id":"not-a-uuid","text":"private transcript","timestamp":"2026-08-02T10:00:00Z","source":"mixed","speaker":"Unknown"}],"skipped_silence":false}"#;

        for payload in [missing, malformed] {
            assert!(matches!(
                parse_server_message(payload),
                Err(ProtocolError::InvalidMessage)
            ));
            assert!(!ProtocolError::InvalidMessage
                .to_string()
                .contains("private transcript"));
        }
    }

    #[test]
    fn parses_strict_assist_updates_without_provider_metadata() {
        let translation = r#"{"type":"assist_segment_update","version":1,"transcript_id":"00000000-0000-0000-0000-000000000010","capability":"translation","state":"ready","translated_text":"translated"}"#;
        let simplification = r#"{"type":"assist_segment_update","version":1,"transcript_id":"00000000-0000-0000-0000-000000000010","capability":"simplification","state":"ready","simplified_text":"simple","target_level":"b1"}"#;
        let reply = r#"{"type":"assist_reply_suggestions","version":1,"anchor_transcript_id":"00000000-0000-0000-0000-000000000010","state":"ready","suggestions":[{"text":"reply","tone":"professional"}]}"#;

        assert!(matches!(
            parse_server_message(translation),
            Ok(ServerMessage::AssistSegmentUpdate(_))
        ));
        assert!(matches!(
            parse_server_message(simplification),
            Ok(ServerMessage::AssistSegmentUpdate(_))
        ));
        assert!(matches!(
            parse_server_message(reply),
            Ok(ServerMessage::AssistReplySuggestions(_))
        ));
    }

    #[test]
    fn rejects_contradictory_or_invalid_assist_messages() {
        let contradictory = r#"{"type":"assist_segment_update","version":1,"transcript_id":"00000000-0000-0000-0000-000000000010","capability":"translation","state":"processing","translated_text":"private"}"#;
        let invalid_state = r#"{"type":"assist_reply_suggestions","version":1,"anchor_transcript_id":"00000000-0000-0000-0000-000000000010","state":"unknown"}"#;

        assert!(matches!(
            parse_server_message(contradictory),
            Err(ProtocolError::InvalidMessage)
        ));
        assert!(matches!(
            parse_server_message(invalid_state),
            Err(ProtocolError::InvalidMessage)
        ));
    }
}
