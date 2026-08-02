//! Deterministic float-to-PCM16 conversion for normalized mono audio.

use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum AudioEncodingError {
    #[error("The audio samples are empty.")]
    EmptySamples,
    #[error("The audio samples are invalid.")]
    NonFiniteSample,
    #[error("The audio chunk is invalid.")]
    InvalidChunk,
    #[error("The WAV payload is invalid.")]
    InvalidWav,
}

/// Clamps normalized samples then maps -1.0 to `i16::MIN`, 0.0 to 0, and
/// 1.0 to `i16::MAX`; intermediate positive and negative values scale toward
/// their respective signed endpoint without dithering or normalization.
pub(crate) fn float_to_pcm16(samples: &[f32]) -> Result<Vec<i16>, AudioEncodingError> {
    if samples.is_empty() {
        return Err(AudioEncodingError::EmptySamples);
    }
    samples
        .iter()
        .map(|sample| {
            if !sample.is_finite() {
                return Err(AudioEncodingError::NonFiniteSample);
            }
            let sample = sample.clamp(-1.0, 1.0);
            Ok(if sample <= -1.0 {
                i16::MIN
            } else {
                (sample * i16::MAX as f32).round() as i16
            })
        })
        .collect()
}

pub(crate) fn pcm16_to_le_bytes(samples: &[i16]) -> Result<Vec<u8>, AudioEncodingError> {
    if samples.is_empty() {
        return Err(AudioEncodingError::EmptySamples);
    }
    Ok(samples
        .iter()
        .flat_map(|sample| sample.to_le_bytes())
        .collect())
}

#[cfg(test)]
mod tests {
    use super::{float_to_pcm16, pcm16_to_le_bytes, AudioEncodingError};
    #[test]
    fn maps_endpoints_clamps_and_preserves_order() {
        assert_eq!(
            float_to_pcm16(&[-1.0, 0.0, 1.0, 2.0, -2.0]).expect("encodes"),
            vec![i16::MIN, 0, i16::MAX, i16::MAX, i16::MIN]
        );
    }
    #[test]
    fn rejects_nonfinite_and_encodes_little_endian() {
        assert_eq!(
            float_to_pcm16(&[f32::NAN]),
            Err(AudioEncodingError::NonFiniteSample)
        );
        assert_eq!(
            float_to_pcm16(&[f32::INFINITY]),
            Err(AudioEncodingError::NonFiniteSample)
        );
        assert_eq!(
            pcm16_to_le_bytes(&[0x1234_i16, -1]).expect("bytes"),
            vec![0x34, 0x12, 0xff, 0xff]
        );
    }
}
