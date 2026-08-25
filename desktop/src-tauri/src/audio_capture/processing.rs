//! Synchronous consumer-side native frame processing pipeline.

use super::{
    mixer::{AudioMixer, MixedAudioFrame, NormalizedAudioFrame},
    resampler::{normalize_to_mono, AudioProcessingError, LinearResampler},
    types::{NativeAudioFrame, NativeAudioSource},
};

pub(crate) struct AudioFrameProcessor {
    system: LinearResampler,
    microphone: LinearResampler,
    mixer: AudioMixer,
}

impl Default for AudioFrameProcessor {
    fn default() -> Self {
        Self {
            system: LinearResampler::default(),
            microphone: LinearResampler::default(),
            mixer: AudioMixer::direct(),
        }
    }
}

impl AudioFrameProcessor {
    pub(crate) fn set_common_timeline_mixing(&mut self, enabled: bool) {
        self.mixer.set_common_timeline_enabled(enabled);
    }

    pub(crate) fn process(
        &mut self,
        frame: NativeAudioFrame,
    ) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        let samples = normalize_to_mono(&frame)?;
        let output = match frame.source() {
            NativeAudioSource::SystemAudio => self
                .system
                .process(frame.format().sample_rate_hz(), samples)?,
            NativeAudioSource::Microphone => self
                .microphone
                .process(frame.format().sample_rate_hz(), samples)?,
        };
        if output.is_empty() {
            return Ok(Vec::new());
        }
        self.mixer.push(NormalizedAudioFrame {
            source: frame.source(),
            capture_time_seconds: frame.capture_time_seconds(),
            samples: output,
        })
    }

    /// Releases the bounded common-timeline tail during an orderly capture
    /// stop. The resamplers retain their established streaming behavior.
    pub(crate) fn flush(&mut self) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        self.mixer.flush()
    }

    pub(crate) fn reset(&mut self) {
        self.system.reset();
        self.microphone.reset();
        self.mixer.reset();
    }
}

#[cfg(test)]
mod tests {
    use super::AudioFrameProcessor;
    use crate::audio_capture::types::{
        NativeAudioFormat, NativeAudioFrame, NativeAudioSamples, NativeAudioSource,
        NativeSampleFormat,
    };

    fn native_frame(
        source: NativeAudioSource,
        timestamp: f64,
        sample_count: usize,
    ) -> NativeAudioFrame {
        NativeAudioFrame::new(
            source,
            NativeAudioFormat::new(48_000, 1, NativeSampleFormat::Float32, true).expect("format"),
            timestamp,
            NativeAudioSamples::Float32(vec![1.0; sample_count]),
        )
        .expect("native frame")
    }

    #[test]
    fn common_timeline_processor_merges_overlapping_normalized_source_frames() {
        let mut processor = AudioFrameProcessor::default();
        processor.set_common_timeline_mixing(true);

        assert!(processor
            .process(native_frame(NativeAudioSource::SystemAudio, 1.0, 960))
            .expect("system processing")
            .is_empty());
        assert!(processor
            .process(native_frame(
                NativeAudioSource::Microphone,
                1.0 + 100.0 / 16_000.0,
                512,
            ))
            .expect("microphone processing")
            .is_empty());

        let frames = processor.flush().expect("common timeline flush");
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].samples.len(), 320);
        assert!(frames[0].samples[..100].iter().all(|sample| *sample == 0.5));
        assert!(frames[0].samples[100..271]
            .iter()
            .all(|sample| *sample == 1.0));
        assert!(frames[0].samples[271..].iter().all(|sample| *sample == 0.5));
    }
}
