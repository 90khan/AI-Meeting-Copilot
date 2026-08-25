//! Bounded common-timeline mixing for normalized 16 kHz mono sources.
//!
//! ScreenCaptureKit delivers System Audio and Microphone buffers on separate
//! callbacks. Their presentation timestamps use the same capture clock, but
//! valid intervals can overlap (for example, a 20 ms system frame and a
//! 10.7 ms microphone frame). The mixer therefore projects each source onto
//! one 16 kHz sample timeline and combines samples at identical timeline
//! positions before handing a strictly monotonic stream to `AudioChunker`.

use std::collections::BTreeMap;

use super::{chunker::SAMPLE_RATE_HZ, resampler::AudioProcessingError, types::NativeAudioSource};

pub(crate) const SYSTEM_AUDIO_GAIN: f32 = 0.5;
pub(crate) const MICROPHONE_GAIN: f32 = 0.5;

/// Retains a small future window while waiting for an overlapping source
/// frame. This is separate from the native-frame PTS reorder holdback.
const COMMON_TIMELINE_HOLDBACK_SAMPLES: i64 = 800; // 50 ms at 16 kHz.
/// Limits temporary insertion work, including for malformed synthetic frames.
const COMMON_TIMELINE_INSERT_BLOCK_SAMPLES: usize = 320; // 20 ms at 16 kHz.
/// Hard memory bound for samples retained while awaiting possible overlap.
const COMMON_TIMELINE_MAX_PENDING_SAMPLES: usize = 4_800; // 300 ms at 16 kHz.

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

#[derive(Debug, Default, Clone, Copy)]
struct TimelineSample {
    system: Option<f32>,
    microphone: Option<f32>,
}

impl TimelineSample {
    fn insert(&mut self, source: NativeAudioSource, sample: f32) {
        match source {
            NativeAudioSource::SystemAudio => self.system = Some(sample),
            NativeAudioSource::Microphone => self.microphone = Some(sample),
        }
    }

    fn mixed(self) -> f32 {
        (self.system.unwrap_or_default() * SYSTEM_AUDIO_GAIN
            + self.microphone.unwrap_or_default() * MICROPHONE_GAIN)
            .clamp(-1.0, 1.0)
    }
}

/// A sample-addressed bounded common timeline. Native PTS values are never
/// modified; the resulting PCM is placed on the required 16 kHz sample grid.
pub(crate) struct AudioMixer {
    common_timeline_enabled: bool,
    timeline_origin_seconds: Option<f64>,
    pending: BTreeMap<i64, TimelineSample>,
    latest_end_index: Option<i64>,
    emitted_end_index: Option<i64>,
    direct_last_time: Option<f64>,
}

impl Default for AudioMixer {
    fn default() -> Self {
        Self {
            common_timeline_enabled: true,
            timeline_origin_seconds: None,
            pending: BTreeMap::new(),
            latest_end_index: None,
            emitted_end_index: None,
            direct_last_time: None,
        }
    }
}

impl AudioMixer {
    /// Single-source capture preserves the existing immediate path. Mixed
    /// capture explicitly enables the bounded common timeline at startup.
    pub(crate) fn direct() -> Self {
        Self {
            common_timeline_enabled: false,
            ..Self::default()
        }
    }

    pub(crate) fn set_common_timeline_enabled(&mut self, enabled: bool) {
        self.reset();
        self.common_timeline_enabled = enabled;
    }

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
        if !self.common_timeline_enabled {
            return self.push_direct(frame);
        }

        let origin = *self
            .timeline_origin_seconds
            .get_or_insert(frame.capture_time_seconds);
        let start_index = timeline_index(origin, frame.capture_time_seconds)?;
        let frame_end_index = start_index
            .checked_add(
                i64::try_from(frame.samples.len())
                    .map_err(|_| AudioProcessingError::InvalidTimeline)?,
            )
            .ok_or(AudioProcessingError::InvalidTimeline)?;
        if self
            .emitted_end_index
            .is_some_and(|emitted_end_index| start_index < emitted_end_index)
        {
            // The frame arrived beyond the bounded overlap window. Do not
            // rewrite its timestamp or silently discard its samples.
            return Err(AudioProcessingError::InvalidTimeline);
        }

        let mut output = Vec::new();
        for (offset, samples) in frame
            .samples
            .chunks(COMMON_TIMELINE_INSERT_BLOCK_SAMPLES)
            .enumerate()
        {
            let block_start = start_index
                .checked_add(
                    i64::try_from(offset * COMMON_TIMELINE_INSERT_BLOCK_SAMPLES)
                        .map_err(|_| AudioProcessingError::InvalidTimeline)?,
                )
                .ok_or(AudioProcessingError::InvalidTimeline)?;
            for (sample_offset, sample) in samples.iter().copied().enumerate() {
                let index = block_start
                    .checked_add(
                        i64::try_from(sample_offset)
                            .map_err(|_| AudioProcessingError::InvalidTimeline)?,
                    )
                    .ok_or(AudioProcessingError::InvalidTimeline)?;
                if !self.pending.contains_key(&index)
                    && self.pending.len() >= COMMON_TIMELINE_MAX_PENDING_SAMPLES
                {
                    return Err(AudioProcessingError::InvalidTimeline);
                }
                self.pending
                    .entry(index)
                    .or_default()
                    .insert(frame.source, sample);
            }
        }
        self.latest_end_index = Some(
            self.latest_end_index
                .unwrap_or(frame_end_index)
                .max(frame_end_index),
        );
        output.extend(self.drain_ready()?);
        Ok(output)
    }

    /// Releases remaining PCM during the normal capture-stop flush.
    pub(crate) fn flush(&mut self) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        if !self.common_timeline_enabled {
            return Ok(Vec::new());
        }
        self.drain_through(i64::MAX)
    }

    pub(crate) fn reset(&mut self) {
        self.timeline_origin_seconds = None;
        self.pending.clear();
        self.latest_end_index = None;
        self.emitted_end_index = None;
        self.direct_last_time = None;
    }

    fn drain_ready(&mut self) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        let Some(latest_end_index) = self.latest_end_index else {
            return Ok(Vec::new());
        };
        self.drain_through(latest_end_index.saturating_sub(COMMON_TIMELINE_HOLDBACK_SAMPLES))
    }

    fn push_direct(
        &mut self,
        frame: NormalizedAudioFrame,
    ) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        if self
            .direct_last_time
            .is_some_and(|previous| frame.capture_time_seconds < previous)
        {
            return Err(AudioProcessingError::InvalidTimeline);
        }
        self.direct_last_time = Some(frame.capture_time_seconds);
        let gain = match frame.source {
            NativeAudioSource::SystemAudio => SYSTEM_AUDIO_GAIN,
            NativeAudioSource::Microphone => MICROPHONE_GAIN,
        };
        Ok(vec![MixedAudioFrame {
            capture_time_seconds: frame.capture_time_seconds,
            samples: frame
                .samples
                .into_iter()
                .map(|sample| (sample * gain).clamp(-1.0, 1.0))
                .collect(),
        }])
    }

    fn drain_through(
        &mut self,
        exclusive_end_index: i64,
    ) -> Result<Vec<MixedAudioFrame>, AudioProcessingError> {
        let Some(origin) = self.timeline_origin_seconds else {
            return Ok(Vec::new());
        };
        let ready_indexes: Vec<i64> = self
            .pending
            .range(..exclusive_end_index)
            .map(|(index, _)| *index)
            .collect();
        let mut output = Vec::new();
        let mut current_start = None;
        let mut current_samples = Vec::new();
        let mut previous_index = None;

        for index in ready_indexes {
            let sample = self
                .pending
                .remove(&index)
                .ok_or(AudioProcessingError::InvalidTimeline)?
                .mixed();
            if previous_index.is_some_and(|previous| index != previous + 1) {
                append_mixed_frame(&mut output, origin, current_start, &mut current_samples)?;
                current_start = None;
            }
            if current_start.is_none() {
                current_start = Some(index);
            }
            current_samples.push(sample);
            previous_index = Some(index);
            self.emitted_end_index = Some(index + 1);
        }
        append_mixed_frame(&mut output, origin, current_start, &mut current_samples)?;
        Ok(output)
    }
}

fn timeline_index(origin: f64, timestamp: f64) -> Result<i64, AudioProcessingError> {
    let sample_position = (timestamp - origin) * SAMPLE_RATE_HZ as f64;
    if !sample_position.is_finite()
        || sample_position < i64::MIN as f64
        || sample_position > i64::MAX as f64
    {
        return Err(AudioProcessingError::InvalidTimeline);
    }
    Ok(sample_position.round() as i64)
}

fn append_mixed_frame(
    output: &mut Vec<MixedAudioFrame>,
    origin: f64,
    start_index: Option<i64>,
    samples: &mut Vec<f32>,
) -> Result<(), AudioProcessingError> {
    let Some(start_index) = start_index else {
        return Ok(());
    };
    if samples.is_empty() {
        return Err(AudioProcessingError::InvalidTimeline);
    }
    output.push(MixedAudioFrame {
        capture_time_seconds: origin + start_index as f64 / SAMPLE_RATE_HZ as f64,
        samples: std::mem::take(samples),
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        AudioMixer, MixedAudioFrame, NormalizedAudioFrame, MICROPHONE_GAIN, SYSTEM_AUDIO_GAIN,
    };
    use crate::audio_capture::{chunker::AudioChunker, types::NativeAudioSource};

    fn frame(
        source: NativeAudioSource,
        time: f64,
        samples: usize,
        value: f32,
    ) -> NormalizedAudioFrame {
        NormalizedAudioFrame {
            source,
            capture_time_seconds: time,
            samples: vec![value; samples],
        }
    }

    fn flush(mixer: &mut AudioMixer) -> Vec<MixedAudioFrame> {
        mixer.flush().expect("valid common timeline")
    }

    #[test]
    fn mixes_partially_overlapping_system_and_microphone_intervals() {
        let mut mixer = AudioMixer::default();
        mixer
            .push(frame(NativeAudioSource::SystemAudio, 1.0, 320, 1.0))
            .expect("system frame");
        mixer
            .push(frame(
                NativeAudioSource::Microphone,
                1.0 + 100.0 / 16_000.0,
                160,
                1.0,
            ))
            .expect("microphone frame");

        let frames = flush(&mut mixer);
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].samples.len(), 320);
        assert_eq!(&frames[0].samples[..100], vec![SYSTEM_AUDIO_GAIN; 100]);
        assert_eq!(&frames[0].samples[100..260], vec![1.0; 160]);
        assert_eq!(&frames[0].samples[260..], vec![SYSTEM_AUDIO_GAIN; 60]);
    }

    #[test]
    fn mixes_fully_overlapping_frames_and_simultaneous_starts() {
        let mut mixer = AudioMixer::default();
        mixer
            .push(frame(NativeAudioSource::SystemAudio, 2.0, 171, 0.8))
            .expect("system frame");
        mixer
            .push(frame(NativeAudioSource::Microphone, 2.0, 171, 0.4))
            .expect("microphone frame");

        assert_eq!(
            flush(&mut mixer),
            vec![MixedAudioFrame {
                capture_time_seconds: 2.0,
                samples: vec![0.6; 171],
            }]
        );
    }

    #[test]
    fn preserves_different_source_cadences_on_one_timeline() {
        let mut mixer = AudioMixer::default();
        mixer
            .push(frame(NativeAudioSource::SystemAudio, 3.0, 320, 1.0))
            .expect("20 ms system frame");
        mixer
            .push(frame(NativeAudioSource::Microphone, 3.0, 171, 1.0))
            .expect("10.7 ms microphone frame");
        mixer
            .push(frame(
                NativeAudioSource::Microphone,
                3.0 + 171.0 / 16_000.0,
                171,
                1.0,
            ))
            .expect("next microphone frame");

        let frames = flush(&mut mixer);
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].samples.len(), 342);
        assert!(frames[0].samples[..320].iter().all(|sample| *sample == 1.0));
        assert!(frames[0].samples[320..]
            .iter()
            .all(|sample| *sample == MICROPHONE_GAIN));
    }

    #[test]
    fn preserves_gaps_and_temporary_source_absence() {
        let mut mixer = AudioMixer::default();
        mixer
            .push(frame(NativeAudioSource::SystemAudio, 4.0, 160, 1.0))
            .expect("first system frame");
        mixer
            .push(frame(NativeAudioSource::SystemAudio, 4.020, 160, 1.0))
            .expect("later system frame");

        let frames = flush(&mut mixer);
        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0].capture_time_seconds, 4.0);
        assert_eq!(frames[1].capture_time_seconds, 4.020);
        assert!(frames
            .iter()
            .flat_map(|frame| &frame.samples)
            .all(|sample| *sample == SYSTEM_AUDIO_GAIN));
    }

    #[test]
    fn preserves_ordered_non_overlapping_input() {
        let mut mixer = AudioMixer::default();
        mixer
            .push(frame(NativeAudioSource::SystemAudio, 5.0, 160, 1.0))
            .expect("system frame");
        mixer
            .push(frame(NativeAudioSource::Microphone, 5.010, 160, 1.0))
            .expect("microphone frame");

        let frames = flush(&mut mixer);
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].capture_time_seconds, 5.0);
        assert_eq!(&frames[0].samples[..160], vec![SYSTEM_AUDIO_GAIN; 160]);
        assert_eq!(&frames[0].samples[160..], vec![MICROPHONE_GAIN; 160]);
    }

    #[test]
    fn retains_single_source_behavior() {
        for source in [
            NativeAudioSource::SystemAudio,
            NativeAudioSource::Microphone,
        ] {
            let mut mixer = AudioMixer::direct();
            let frames = mixer.push(frame(source, 6.0, 160, 1.0)).expect("frame");
            assert_eq!(frames.len(), 1);
            assert_eq!(frames[0].capture_time_seconds, 6.0);
            assert_eq!(
                frames[0].samples,
                vec![
                    if source == NativeAudioSource::SystemAudio {
                        SYSTEM_AUDIO_GAIN
                    } else {
                        MICROPHONE_GAIN
                    };
                    160
                ]
            );
        }
    }

    #[test]
    fn long_mixed_sequence_stays_monotonic_for_the_chunker() {
        let mut mixer = AudioMixer::default();
        let mut chunker = AudioChunker::default();
        let mut next_system = 0_usize;
        let mut next_microphone = 0_usize;
        while next_system < 100 || next_microphone < 188 {
            let system_time = next_system as f64 * 320.0 / 16_000.0;
            let microphone_time = next_microphone as f64 * 171.0 / 16_000.0;
            let source = if next_system < 100
                && (next_microphone >= 188 || system_time <= microphone_time)
            {
                next_system += 1;
                NativeAudioSource::SystemAudio
            } else {
                next_microphone += 1;
                NativeAudioSource::Microphone
            };
            let (time, samples) = if source == NativeAudioSource::SystemAudio {
                (system_time, 320)
            } else {
                (microphone_time, 171)
            };
            for mixed in mixer
                .push(frame(source, time, samples, 0.0))
                .expect("mixer push")
            {
                chunker.push(mixed).expect("monotonic mixed frame");
            }
        }
        for mixed in flush(&mut mixer) {
            chunker.push(mixed).expect("monotonic flushed frame");
        }
    }

    #[test]
    fn common_timeline_has_a_strict_pending_sample_bound() {
        let mut mixer = AudioMixer::default();
        assert!(mixer
            .push(frame(NativeAudioSource::SystemAudio, 7.0, 4_801, 0.0))
            .is_err());
        assert!(mixer.pending.len() <= super::COMMON_TIMELINE_MAX_PENDING_SAMPLES);
    }
}
