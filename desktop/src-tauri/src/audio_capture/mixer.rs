//! Timestamp-directed mono source synchronization and fixed-gain mixing.

use std::collections::VecDeque;

use super::{resampler::AudioProcessingError, types::NativeAudioSource};

pub(crate) const SYSTEM_AUDIO_GAIN: f32 = 0.5;
pub(crate) const MICROPHONE_GAIN: f32 = 0.5;
const SYNC_TOLERANCE_SECONDS: f64 = 0.050;

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct NormalizedAudioFrame {
    pub(crate) source: NativeAudioSource,
    pub(crate) capture_time_seconds: f64,
    pub(crate) samples: Vec<f32>,
}
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct MixedAudioFrame {
    pub(crate) capture_time_seconds: f64,
    pub(crate) samples: Vec<f32>,
}

pub(crate) struct AudioMixer {
    system: VecDeque<NormalizedAudioFrame>,
    microphone: VecDeque<NormalizedAudioFrame>,
    last_time: f64,
}
impl Default for AudioMixer {
    fn default() -> Self {
        Self {
            system: VecDeque::new(),
            microphone: VecDeque::new(),
            last_time: 0.0,
        }
    }
}
impl AudioMixer {
    pub(crate) fn push(
        &mut self,
        frame: NormalizedAudioFrame,
    ) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        if !frame.capture_time_seconds.is_finite()
            || frame.capture_time_seconds < 0.0
            || frame.samples.is_empty()
        {
            return Err(AudioProcessingError::InvalidTimeline);
        }
        match frame.source {
            NativeAudioSource::SystemAudio => self.system.push_back(frame),
            NativeAudioSource::Microphone => self.microphone.push_back(frame),
        }
        let mut output = Vec::new();
        while !self.system.is_empty() || !self.microphone.is_empty() {
            let system = self.system.pop_front();
            let microphone = self.microphone.pop_front();
            let timestamp = system
                .as_ref()
                .map(|f| f.capture_time_seconds)
                .unwrap_or(f64::INFINITY)
                .min(
                    microphone
                        .as_ref()
                        .map(|f| f.capture_time_seconds)
                        .unwrap_or(f64::INFINITY),
                )
                .max(self.last_time);
            match (system, microphone) {
                (Some(system), Some(microphone)) => {
                    let count = system.samples.len().min(microphone.samples.len());
                    let samples = (0..count)
                        .map(|i| {
                            (system.samples[i] * SYSTEM_AUDIO_GAIN
                                + microphone.samples[i] * MICROPHONE_GAIN)
                                .clamp(-1.0, 1.0)
                        })
                        .collect();
                    output.push(MixedAudioFrame {
                        capture_time_seconds: timestamp,
                        samples,
                    });
                }
                (Some(frame), None) => output.push(MixedAudioFrame {
                    capture_time_seconds: timestamp,
                    samples: frame
                        .samples
                        .into_iter()
                        .map(|v| (v * SYSTEM_AUDIO_GAIN).clamp(-1.0, 1.0))
                        .collect(),
                }),
                (None, Some(frame)) => output.push(MixedAudioFrame {
                    capture_time_seconds: timestamp,
                    samples: frame
                        .samples
                        .into_iter()
                        .map(|v| (v * MICROPHONE_GAIN).clamp(-1.0, 1.0))
                        .collect(),
                }),
                (None, None) => break,
            }
            self.last_time = timestamp;
        }
        Ok(output)
    }
    pub(crate) fn reset(&mut self) {
        self.system.clear();
        self.microphone.clear();
        self.last_time = 0.0;
    }
}
