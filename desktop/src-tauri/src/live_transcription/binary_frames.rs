//! AMCP binary WAV-frame construction for the local live-transcription protocol.

#![allow(dead_code)] // Audio ingress will call this internal boundary in the next task.

use serde_json::json;
use thiserror::Error;
use uuid::Uuid;

use super::protocol::{AudioSource, AUDIO_FRAME_MAGIC, AUDIO_MESSAGE_KIND, PROTOCOL_VERSION};

const HEADER_LENGTH: usize = 8;
pub const DEFAULT_MAX_BINARY_PAYLOAD_BYTES: usize = 524_288;

/// Metadata that immediately precedes an immutable WAV payload.
#[derive(Clone, PartialEq)]
pub(crate) struct AudioChunkFrameMetadata {
    pub(crate) session_id: Uuid,
    pub(crate) sequence: u64,
    pub(crate) capture_started_at: String,
    pub(crate) source: AudioSource,
    pub(crate) sample_rate_hz: u32,
    pub(crate) channels: u8,
    pub(crate) overlap_seconds: f64,
    pub(crate) upstream_pending_chunks: u8,
    pub(crate) byte_length: usize,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum BinaryFrameError {
    #[error("The audio chunk is invalid.")]
    Invalid,
    #[error("The audio chunk exceeds the permitted size.")]
    TooLarge,
}

/// Build the exact V1 AMCP frame: header, compact JSON metadata, then WAV bytes.
pub(crate) fn build_audio_chunk_frame(
    metadata: &AudioChunkFrameMetadata,
    wav_payload: &[u8],
    max_payload_bytes: usize,
) -> Result<Vec<u8>, BinaryFrameError> {
    validate_metadata(metadata, wav_payload, max_payload_bytes)?;

    let metadata_bytes = serde_json::to_vec(&json!({
        "audio_format": "wav",
        "byte_length": metadata.byte_length,
        "capture_started_at": metadata.capture_started_at,
        "channels": metadata.channels,
        "overlap_seconds": metadata.overlap_seconds,
        "sample_rate_hz": metadata.sample_rate_hz,
        "sequence": metadata.sequence,
        "session_id": metadata.session_id.to_string(),
        "source": metadata.source.as_str(),
        "upstream_pending_chunks": metadata.upstream_pending_chunks,
    }))
    .map_err(|_| BinaryFrameError::Invalid)?;
    let metadata_length =
        u16::try_from(metadata_bytes.len()).map_err(|_| BinaryFrameError::Invalid)?;

    let mut frame = Vec::with_capacity(HEADER_LENGTH + metadata_bytes.len() + wav_payload.len());
    frame.extend_from_slice(AUDIO_FRAME_MAGIC);
    frame.push(PROTOCOL_VERSION);
    frame.push(AUDIO_MESSAGE_KIND);
    frame.extend_from_slice(&metadata_length.to_be_bytes());
    frame.extend_from_slice(&metadata_bytes);
    frame.extend_from_slice(wav_payload);
    Ok(frame)
}

fn validate_metadata(
    metadata: &AudioChunkFrameMetadata,
    wav_payload: &[u8],
    max_payload_bytes: usize,
) -> Result<(), BinaryFrameError> {
    if max_payload_bytes == 0 || wav_payload.is_empty() || wav_payload.len() > max_payload_bytes {
        return Err(BinaryFrameError::TooLarge);
    }
    if metadata.byte_length != wav_payload.len()
        || metadata.sample_rate_hz != 16_000
        || metadata.channels != 1
        || !metadata.overlap_seconds.is_finite()
        || metadata.overlap_seconds < 0.0
        || !is_utc_timestamp(&metadata.capture_started_at)
    {
        return Err(BinaryFrameError::Invalid);
    }
    Ok(())
}

fn is_utc_timestamp(value: &str) -> bool {
    value.contains('T') && value.ends_with('Z')
}

#[cfg(test)]
mod tests {
    use super::{build_audio_chunk_frame, AudioChunkFrameMetadata, BinaryFrameError};
    use crate::live_transcription::protocol::AudioSource;
    use uuid::Uuid;

    fn metadata(byte_length: usize) -> AudioChunkFrameMetadata {
        AudioChunkFrameMetadata {
            session_id: Uuid::nil(),
            sequence: 0,
            capture_started_at: "2026-08-02T10:00:00Z".to_owned(),
            source: AudioSource::Mixed,
            sample_rate_hz: 16_000,
            channels: 1,
            overlap_seconds: 0.5,
            upstream_pending_chunks: 0,
            byte_length,
        }
    }

    #[test]
    fn builds_a_compact_amcp_frame() {
        let payload = [1_u8, 2, 3];
        let frame =
            build_audio_chunk_frame(&metadata(payload.len()), &payload, 10).expect("frame builds");

        assert_eq!(&frame[..4], b"AMCP");
        assert_eq!(frame[4], 1);
        assert_eq!(frame[5], 1);
        let metadata_length = u16::from_be_bytes([frame[6], frame[7]]) as usize;
        assert_eq!(&frame[8 + metadata_length..], payload);
        assert!(!frame[8..8 + metadata_length].contains(&b' '));
        let parsed_metadata: serde_json::Value =
            serde_json::from_slice(&frame[8..8 + metadata_length]).expect("metadata is JSON");
        assert_eq!(parsed_metadata["upstream_pending_chunks"], 0);
    }

    #[test]
    fn rejects_mismatched_or_oversized_payloads() {
        let payload = [1_u8, 2, 3];
        assert_eq!(
            build_audio_chunk_frame(&metadata(2), &payload, 10),
            Err(BinaryFrameError::Invalid)
        );
        assert_eq!(
            build_audio_chunk_frame(&metadata(3), &payload, 2),
            Err(BinaryFrameError::TooLarge)
        );
    }
}
