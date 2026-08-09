//! Private, bounded decoder for the sidecar playback stream.

use std::fmt;

pub(crate) const PLAYBACK_FRAME_VERSION: u8 = 1;
pub(crate) const PLAYBACK_FRAME_HEADER_BYTES: usize = 10;
/// Matches the backend's per-segment plaintext limit; it is intentionally not
/// derived from untrusted stream metadata.
pub(crate) const MAX_PLAYBACK_FRAME_PAYLOAD_BYTES: usize = 67_108_864;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum PlaybackFrameKind {
    AudioSegment,
    End,
    Error,
}

impl fmt::Debug for PlaybackFrameKind {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let name = match self {
            Self::AudioSegment => "AudioSegment",
            Self::End => "End",
            Self::Error => "Error",
        };
        formatter.write_str(name)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct PlaybackFrameHeader {
    pub(crate) version: u8,
    pub(crate) kind: PlaybackFrameKind,
    pub(crate) segment_index: u32,
    pub(crate) payload_length: u32,
}

#[derive(PartialEq, Eq)]
pub(crate) struct PlaybackFrame {
    pub(crate) header: PlaybackFrameHeader,
    pub(crate) payload: Vec<u8>,
}

impl fmt::Debug for PlaybackFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PlaybackFrame")
            .field("header", &self.header)
            .field("payload_length", &self.payload.len())
            .finish()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct PlaybackProtocolError;

impl fmt::Debug for PlaybackProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PlaybackProtocolError")
    }
}

impl fmt::Display for PlaybackProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Playback stream is invalid.")
    }
}

impl std::error::Error for PlaybackProtocolError {}

pub(crate) fn parse_header(bytes: &[u8]) -> Result<PlaybackFrameHeader, PlaybackProtocolError> {
    if bytes.len() != PLAYBACK_FRAME_HEADER_BYTES {
        return Err(PlaybackProtocolError);
    }
    if bytes[0] != PLAYBACK_FRAME_VERSION {
        return Err(PlaybackProtocolError);
    }
    let kind = match bytes[1] {
        1 => PlaybackFrameKind::AudioSegment,
        2 => PlaybackFrameKind::End,
        3 => PlaybackFrameKind::Error,
        _ => return Err(PlaybackProtocolError),
    };
    let segment_index =
        u32::from_be_bytes(bytes[2..6].try_into().map_err(|_| PlaybackProtocolError)?);
    let payload_length =
        u32::from_be_bytes(bytes[6..10].try_into().map_err(|_| PlaybackProtocolError)?);
    if payload_length as usize > MAX_PLAYBACK_FRAME_PAYLOAD_BYTES
        || matches!(kind, PlaybackFrameKind::End | PlaybackFrameKind::Error) && payload_length != 0
    {
        return Err(PlaybackProtocolError);
    }
    Ok(PlaybackFrameHeader {
        version: bytes[0],
        kind,
        segment_index,
        payload_length,
    })
}

/// Buffers only one frame plus any partial HTTP chunk, never an entire recording.
pub(crate) struct PlaybackFrameDecoder {
    buffered: Vec<u8>,
}

impl PlaybackFrameDecoder {
    pub(crate) fn new() -> Self {
        Self {
            buffered: Vec::new(),
        }
    }

    pub(crate) fn push(&mut self, bytes: &[u8]) {
        self.buffered.extend_from_slice(bytes);
    }

    pub(crate) fn next_frame(&mut self) -> Result<Option<PlaybackFrame>, PlaybackProtocolError> {
        if self.buffered.len() < PLAYBACK_FRAME_HEADER_BYTES {
            return Ok(None);
        }
        let header = parse_header(&self.buffered[..PLAYBACK_FRAME_HEADER_BYTES])?;
        let frame_size = PLAYBACK_FRAME_HEADER_BYTES
            .checked_add(header.payload_length as usize)
            .ok_or(PlaybackProtocolError)?;
        if self.buffered.len() < frame_size {
            return Ok(None);
        }
        let payload = self.buffered[PLAYBACK_FRAME_HEADER_BYTES..frame_size].to_vec();
        self.buffered.drain(..frame_size);
        Ok(Some(PlaybackFrame { header, payload }))
    }

    pub(crate) fn finish(&self) -> Result<(), PlaybackProtocolError> {
        if self.buffered.is_empty() {
            Ok(())
        } else {
            Err(PlaybackProtocolError)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        PlaybackFrameDecoder, PlaybackFrameKind, MAX_PLAYBACK_FRAME_PAYLOAD_BYTES,
        PLAYBACK_FRAME_VERSION,
    };

    fn frame(kind: u8, index: u32, payload: &[u8]) -> Vec<u8> {
        let mut bytes = vec![PLAYBACK_FRAME_VERSION, kind];
        bytes.extend_from_slice(&index.to_be_bytes());
        bytes.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        bytes.extend_from_slice(payload);
        bytes
    }

    #[test]
    fn decoder_accepts_audio_and_terminal_frames_incrementally() {
        let audio = frame(1, 3, b"audio");
        let end = frame(2, 4, b"");
        let mut decoder = PlaybackFrameDecoder::new();
        decoder.push(&audio[..4]);
        assert!(decoder
            .next_frame()
            .expect("partial header is valid")
            .is_none());
        decoder.push(&audio[4..]);
        decoder.push(&end);

        let decoded = decoder
            .next_frame()
            .expect("audio frame is valid")
            .expect("frame");
        assert_eq!(decoded.header.kind, PlaybackFrameKind::AudioSegment);
        assert_eq!(decoded.header.segment_index, 3);
        assert_eq!(decoded.payload, b"audio");
        assert_eq!(
            decoder
                .next_frame()
                .expect("end frame is valid")
                .expect("frame")
                .header
                .kind,
            PlaybackFrameKind::End
        );
        assert!(decoder.finish().is_ok());

        let mut error_decoder = PlaybackFrameDecoder::new();
        error_decoder.push(&frame(3, 5, b""));
        assert_eq!(
            error_decoder
                .next_frame()
                .expect("error frame is valid")
                .expect("frame")
                .header
                .kind,
            PlaybackFrameKind::Error
        );
    }

    #[test]
    fn decoder_rejects_invalid_headers_and_terminal_payloads() {
        let mut bad_version = frame(1, 0, b"");
        bad_version[0] = 2;
        let mut unknown_kind = frame(9, 0, b"");
        let terminal_payload = frame(2, 0, b"x");
        let error_payload = frame(3, 0, b"x");
        let oversized = {
            let mut bytes = vec![PLAYBACK_FRAME_VERSION, 1];
            bytes.extend_from_slice(&0_u32.to_be_bytes());
            bytes.extend_from_slice(&((MAX_PLAYBACK_FRAME_PAYLOAD_BYTES + 1) as u32).to_be_bytes());
            bytes
        };

        for invalid in [
            &bad_version,
            &unknown_kind,
            &terminal_payload,
            &error_payload,
            &oversized,
        ] {
            let mut decoder = PlaybackFrameDecoder::new();
            decoder.push(invalid);
            assert!(decoder.next_frame().is_err());
        }
        unknown_kind.truncate(8);
        let mut truncated = PlaybackFrameDecoder::new();
        truncated.push(&unknown_kind);
        assert!(truncated.finish().is_err());
    }

    #[test]
    fn decoder_rejects_truncated_payload() {
        let mut decoder = PlaybackFrameDecoder::new();
        let frame = frame(1, 0, b"audio");
        decoder.push(&frame[..11]);
        assert!(decoder
            .next_frame()
            .expect("partial payload is not an error")
            .is_none());
        assert!(decoder.finish().is_err());
    }
}
