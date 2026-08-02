//! Strict Rust-local representation of the versioned live-transcription protocol.

#![allow(dead_code)] // Session/audio operations are intentionally not frontend commands yet.

use serde::Serialize;
use serde_json::Value;
use thiserror::Error;
use uuid::Uuid;

pub const PROTOCOL_VERSION: u8 = 1;
pub const AUDIO_FRAME_MAGIC: &[u8; 4] = b"AMCP";
pub const AUDIO_MESSAGE_KIND: u8 = 1;
pub const DEFAULT_MAX_IN_FLIGHT_CHUNKS: u8 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AudioSource {
    Mixed,
    Microphone,
    SystemAudio,
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
) -> Result<String, ProtocolError> {
    if language_hint.is_some_and(|value| value.trim().is_empty()) {
        return Err(ProtocolError::InvalidMessage);
    }
    serde_json::to_string(&StartSessionMessage {
        message_type: "start_session",
        version: PROTOCOL_VERSION,
        request_id,
        meeting_id,
        language_hint,
        source,
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
    Status,
    Error(ProtocolFailure),
    SessionStopped(SessionStopped),
}

/// Internal finalized transcript data retained for the future desktop event router.
pub(crate) struct ChunkResultSegment {
    pub(crate) transcript_id: Uuid,
    pub(crate) text: String,
    pub(crate) timestamp: String,
    pub(crate) source: AudioSource,
    pub(crate) speaker: String,
}

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
        "status" => parse_status(object),
        "error" => parse_error(object),
        "session_stopped" => parse_session_stopped(object),
        _ => Err(ProtocolError::InvalidMessage),
    }
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
    }))
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
    use super::{parse_server_message, serialize_hello, AudioSource, ProtocolError, ServerMessage};
    use uuid::Uuid;

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
}
