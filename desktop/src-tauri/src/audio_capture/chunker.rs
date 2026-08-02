//! Deterministic 16 kHz mono rolling chunk construction.

use thiserror::Error;

use super::mixer::MixedAudioFrame;

pub(crate) const SAMPLE_RATE_HZ: usize = 16_000;
pub(crate) const CHUNK_SAMPLES: usize = 32_000;
pub(crate) const OVERLAP_SAMPLES: usize = 8_000;
pub(crate) const HOP_SAMPLES: usize = CHUNK_SAMPLES - OVERLAP_SAMPLES;
const SAMPLE_SECONDS: f64 = 1.0 / SAMPLE_RATE_HZ as f64;
const GAP_TOLERANCE_SECONDS: f64 = 0.100;

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct AudioChunk {
    pub(crate) sequence: u64,
    pub(crate) capture_started_at_seconds: f64,
    pub(crate) samples: Vec<f32>,
    pub(crate) overlap_seconds: f64,
}

#[derive(Debug, Default, PartialEq)]
pub(crate) struct ChunkingResult {
    pub(crate) chunks: Vec<AudioChunk>,
    pub(crate) gap_detected: bool,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum AudioChunkerError {
    #[error("The mixed audio timeline is invalid.")]
    InvalidTimeline,
}

pub(crate) struct AudioChunker {
    samples: Vec<f32>,
    start_time: Option<f64>,
    expected_next_time: Option<f64>,
    sequence: u64,
}

impl Default for AudioChunker {
    fn default() -> Self {
        Self {
            samples: Vec::new(),
            start_time: None,
            expected_next_time: None,
            sequence: 0,
        }
    }
}

impl AudioChunker {
    pub(crate) fn push(
        &mut self,
        frame: MixedAudioFrame,
    ) -> Result<ChunkingResult, AudioChunkerError> {
        if !frame.capture_time_seconds.is_finite()
            || frame.capture_time_seconds < 0.0
            || frame.samples.is_empty()
        {
            return Err(AudioChunkerError::InvalidTimeline);
        }
        let mut gap_detected = false;
        if let Some(expected) = self.expected_next_time {
            let delta = frame.capture_time_seconds - expected;
            if delta < -SAMPLE_SECONDS {
                return Err(AudioChunkerError::InvalidTimeline);
            }
            if delta.abs() > GAP_TOLERANCE_SECONDS {
                self.samples.clear();
                self.start_time = None;
                gap_detected = true;
            }
        }
        if self.samples.is_empty() {
            self.start_time = Some(frame.capture_time_seconds);
        }
        self.expected_next_time =
            Some(frame.capture_time_seconds + frame.samples.len() as f64 * SAMPLE_SECONDS);
        self.samples.extend(frame.samples);
        Ok(ChunkingResult {
            chunks: self.flush_complete_chunks(),
            gap_detected,
        })
    }

    pub(crate) fn flush_complete_chunks(&mut self) -> Vec<AudioChunk> {
        let mut chunks = Vec::new();
        while self.samples.len() >= CHUNK_SAMPLES {
            let timestamp = self.start_time.expect("buffer start time is set");
            chunks.push(AudioChunk {
                sequence: self.sequence,
                capture_started_at_seconds: timestamp,
                samples: self.samples[..CHUNK_SAMPLES].to_vec(),
                overlap_seconds: 0.5,
            });
            self.sequence += 1;
            self.samples.drain(..HOP_SAMPLES);
            self.start_time = Some(timestamp + HOP_SAMPLES as f64 * SAMPLE_SECONDS);
        }
        chunks
    }
    pub(crate) fn reset(&mut self) {
        self.samples.clear();
        self.start_time = None;
        self.expected_next_time = None;
        self.sequence = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::{AudioChunker, AudioChunkerError, CHUNK_SAMPLES, HOP_SAMPLES, OVERLAP_SAMPLES};
    use crate::audio_capture::mixer::MixedAudioFrame;
    fn frame(time: f64, count: usize, offset: usize) -> MixedAudioFrame {
        MixedAudioFrame {
            capture_time_seconds: time,
            samples: (offset..offset + count).map(|v| v as f32).collect(),
        }
    }
    #[test]
    fn emits_full_chunks_with_exact_overlap_and_sequence() {
        let mut chunker = AudioChunker::default();
        assert!(chunker
            .push(frame(0.0, CHUNK_SAMPLES - 1, 0))
            .expect("push")
            .chunks
            .is_empty());
        let first = chunker
            .push(frame(
                (CHUNK_SAMPLES - 1) as f64 / 16_000.0,
                1,
                CHUNK_SAMPLES - 1,
            ))
            .expect("push")
            .chunks
            .remove(0);
        assert_eq!(first.sequence, 0);
        assert_eq!(first.samples.len(), CHUNK_SAMPLES);
        let second = chunker
            .push(frame(2.0, HOP_SAMPLES, CHUNK_SAMPLES))
            .expect("push")
            .chunks
            .remove(0);
        assert_eq!(second.sequence, 1);
        assert_eq!(
            &first.samples[HOP_SAMPLES..],
            &second.samples[..OVERLAP_SAMPLES]
        );
    }
    #[test]
    fn rejects_backward_time_and_resets_for_gaps() {
        let mut chunker = AudioChunker::default();
        chunker.push(frame(1.0, 100, 0)).expect("push");
        assert_eq!(
            chunker.push(frame(0.0, 10, 0)),
            Err(AudioChunkerError::InvalidTimeline)
        );
        assert!(
            chunker
                .push(frame(2.0, CHUNK_SAMPLES, 0))
                .expect("gap")
                .gap_detected
        );
        chunker.reset();
        assert!(
            chunker
                .push(frame(0.0, CHUNK_SAMPLES, 0))
                .expect("reset")
                .chunks[0]
                .sequence
                == 0
        );
    }
}
