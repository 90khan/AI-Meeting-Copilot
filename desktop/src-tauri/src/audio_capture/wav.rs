//! In-memory PCM16 RIFF/WAVE construction for finalized audio chunks.

use super::{
    chunker::{AudioChunk, SAMPLE_RATE_HZ},
    pcm16::{float_to_pcm16, pcm16_to_le_bytes, AudioEncodingError},
};

const HEADER_BYTES: usize = 44;

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct EncodedAudioChunk {
    pub(crate) sequence: u64,
    pub(crate) capture_started_at_seconds: f64,
    pub(crate) overlap_seconds: f64,
    pub(crate) wav_payload: Vec<u8>,
}

pub(crate) fn build_wav_bytes(chunk: &AudioChunk) -> Result<Vec<u8>, AudioEncodingError> {
    if chunk.samples.is_empty()
        || !chunk.capture_started_at_seconds.is_finite()
        || chunk.capture_started_at_seconds < 0.0
    {
        return Err(AudioEncodingError::InvalidChunk);
    }
    build_wav_from_samples(&chunk.samples)
}

/// Build the same V1 PCM16 WAV contract from canonical mono samples.
pub(crate) fn build_wav_from_samples(samples: &[f32]) -> Result<Vec<u8>, AudioEncodingError> {
    let pcm = pcm16_to_le_bytes(&float_to_pcm16(samples)?)?;
    let data_len = u32::try_from(pcm.len()).map_err(|_| AudioEncodingError::InvalidChunk)?;
    let mut wav = Vec::with_capacity(HEADER_BYTES + pcm.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(
        &(36_u32
            .checked_add(data_len)
            .ok_or(AudioEncodingError::InvalidChunk)?)
        .to_le_bytes(),
    );
    wav.extend_from_slice(b"WAVE");
    wav.extend_from_slice(b"fmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&(SAMPLE_RATE_HZ as u32).to_le_bytes());
    wav.extend_from_slice(&((SAMPLE_RATE_HZ * 2) as u32).to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_len.to_le_bytes());
    wav.extend_from_slice(&pcm);
    Ok(wav)
}

pub(crate) fn encode_audio_chunk(
    chunk: AudioChunk,
) -> Result<EncodedAudioChunk, AudioEncodingError> {
    let wav_payload = build_wav_bytes(&chunk)?;
    Ok(EncodedAudioChunk {
        sequence: chunk.sequence,
        capture_started_at_seconds: chunk.capture_started_at_seconds,
        overlap_seconds: chunk.overlap_seconds,
        wav_payload,
    })
}

#[cfg(test)]
mod tests {
    use super::{build_wav_bytes, encode_audio_chunk, HEADER_BYTES};
    use crate::audio_capture::chunker::{AudioChunk, CHUNK_SAMPLES, OVERLAP_SECONDS};
    #[test]
    fn builds_complete_pcm16_mono_wav_and_preserves_metadata() {
        let chunk = AudioChunk {
            sequence: 4,
            capture_started_at_seconds: 2.0,
            samples: vec![0.0; CHUNK_SAMPLES],
            overlap_seconds: OVERLAP_SECONDS,
        };
        let encoded = encode_audio_chunk(chunk).expect("encodes");
        let wav = &encoded.wav_payload;
        assert_eq!(&wav[..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(u16::from_le_bytes([wav[20], wav[21]]), 1);
        assert_eq!(u16::from_le_bytes([wav[22], wav[23]]), 1);
        assert_eq!(
            u32::from_le_bytes([wav[24], wav[25], wav[26], wav[27]]),
            16_000
        );
        assert_eq!(u16::from_le_bytes([wav[34], wav[35]]), 16);
        assert_eq!(wav.len(), HEADER_BYTES + CHUNK_SAMPLES * 2);
        assert_eq!(encoded.sequence, 4);
    }
    #[test]
    fn wav_rejects_empty_input() {
        let chunk = AudioChunk {
            sequence: 0,
            capture_started_at_seconds: 0.0,
            samples: vec![],
            overlap_seconds: OVERLAP_SECONDS,
        };
        assert!(build_wav_bytes(&chunk).is_err());
    }
}
