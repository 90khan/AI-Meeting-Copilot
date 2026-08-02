//! Synchronous consumer-side native frame processing pipeline.

use super::{
    mixer::{AudioMixer, MixedAudioFrame, NormalizedAudioFrame},
    resampler::{normalize_to_mono, AudioProcessingError, LinearResampler},
    types::{NativeAudioFrame, NativeAudioSource},
};

#[derive(Default)]
pub(crate) struct AudioFrameProcessor {
    system: LinearResampler,
    microphone: LinearResampler,
    mixer: AudioMixer,
}
impl AudioFrameProcessor {
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
    pub(crate) fn reset(&mut self) {
        self.system.reset();
        self.microphone.reset();
        self.mixer.reset();
    }
}
