//! Deterministic native-frame normalization and stateful linear resampling.

use thiserror::Error;

use super::types::{NativeAudioFrame, NativeAudioSamples};

pub(crate) const TARGET_SAMPLE_RATE_HZ: u32 = 16_000;

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum AudioProcessingError {
    #[error("The native audio frame is invalid.")]
    InvalidFrame,
    #[error("The native audio layout is unsupported.")]
    UnsupportedLayout,
    #[error("The audio timeline is invalid.")]
    InvalidTimeline,
}

pub(crate) fn normalize_to_mono(
    frame: &NativeAudioFrame,
) -> Result<Vec<f32>, AudioProcessingError> {
    let channels = usize::from(frame.format().channels());
    let input: Vec<f32> = match frame.samples() {
        NativeAudioSamples::Float32(values) => values.clone(),
        NativeAudioSamples::SignedInt16(values) => values
            .iter()
            .map(|value| f32::from(*value) / 32_768.0)
            .collect(),
    };
    if input.is_empty() || input.len() % channels != 0 {
        return Err(AudioProcessingError::InvalidFrame);
    }
    let frames = input.len() / channels;
    let mut mono = Vec::with_capacity(frames);
    for index in 0..frames {
        let mut sum = 0.0_f32;
        for channel in 0..channels {
            let offset = if frame.format().interleaved() {
                index * channels + channel
            } else {
                channel * frames + index
            };
            sum += input
                .get(offset)
                .ok_or(AudioProcessingError::UnsupportedLayout)?;
        }
        mono.push(sum / channels as f32);
    }
    Ok(mono)
}

/// Stateful linear resampler. One input sample is retained at each boundary
/// so output interpolation never creates a discontinuity across native frames.
#[derive(Default)]
pub(crate) struct LinearResampler {
    input_rate: Option<u32>,
    input_start: u64,
    next_output_position: f64,
    pending: Vec<f32>,
}

impl LinearResampler {
    pub(crate) fn process(
        &mut self,
        input_rate: u32,
        samples: Vec<f32>,
    ) -> Result<Vec<f32>, AudioProcessingError> {
        if input_rate == 0 || samples.is_empty() {
            return Err(AudioProcessingError::InvalidFrame);
        }
        if self.input_rate != Some(input_rate) {
            self.reset();
            self.input_rate = Some(input_rate);
        }
        self.pending.extend(samples);
        let step = f64::from(input_rate) / f64::from(TARGET_SAMPLE_RATE_HZ);
        let end = self.input_start + self.pending.len() as u64;
        let mut output = Vec::new();
        while self.next_output_position + 1.0 < end as f64 {
            let relative = self.next_output_position - self.input_start as f64;
            let lower = relative.floor() as usize;
            let fraction = relative - lower as f64;
            let first = *self
                .pending
                .get(lower)
                .ok_or(AudioProcessingError::InvalidFrame)?;
            let second = *self
                .pending
                .get(lower + 1)
                .ok_or(AudioProcessingError::InvalidFrame)?;
            output.push(first + (second - first) * fraction as f32);
            self.next_output_position += step;
        }
        let discard =
            (self.next_output_position.floor() as u64).saturating_sub(self.input_start) as usize;
        if discard > 0 {
            self.pending.drain(..discard.min(self.pending.len()));
            self.input_start += discard as u64;
        }
        Ok(output)
    }
    pub(crate) fn reset(&mut self) {
        self.input_rate = None;
        self.input_start = 0;
        self.next_output_position = 0.0;
        self.pending.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::{normalize_to_mono, LinearResampler, TARGET_SAMPLE_RATE_HZ};
    use crate::audio_capture::types::{
        NativeAudioFormat, NativeAudioFrame, NativeAudioSamples, NativeAudioSource,
        NativeSampleFormat,
    };

    fn frame(samples: NativeAudioSamples, channels: u16, interleaved: bool) -> NativeAudioFrame {
        let sample_format = match &samples {
            NativeAudioSamples::Float32(_) => NativeSampleFormat::Float32,
            NativeAudioSamples::SignedInt16(_) => NativeSampleFormat::SignedInt16,
        };
        NativeAudioFrame::new(
            NativeAudioSource::SystemAudio,
            NativeAudioFormat::new(48_000, channels, sample_format, interleaved).expect("format"),
            0.0,
            samples,
        )
        .expect("frame")
    }
    #[test]
    fn normalizes_int16_and_both_stereo_layouts() {
        assert_eq!(
            normalize_to_mono(&frame(
                NativeAudioSamples::SignedInt16(vec![32_767]),
                1,
                true
            ))
            .expect("normalizes")[0],
            32_767.0 / 32_768.0
        );
        assert_eq!(
            normalize_to_mono(&frame(
                NativeAudioSamples::Float32(vec![0.0, 1.0, 0.5, -0.5]),
                2,
                true
            ))
            .expect("normalizes"),
            vec![0.5, 0.0]
        );
        assert_eq!(
            normalize_to_mono(&frame(
                NativeAudioSamples::Float32(vec![0.0, 0.5, 1.0, -0.5]),
                2,
                false
            ))
            .expect("normalizes"),
            vec![0.5, 0.0]
        );
    }
    #[test]
    fn resamples_48khz_to_target_rate_with_boundary_state() {
        let mut resampler = LinearResampler::default();
        let first = resampler.process(48_000, vec![0.0; 49]).expect("resamples");
        let second = resampler.process(48_000, vec![0.0; 49]).expect("continues");
        assert_eq!(TARGET_SAMPLE_RATE_HZ, 16_000);
        assert!(!first.is_empty() && !second.is_empty());
    }
}
