//! Deterministic 16 kHz mono rolling chunk construction.

use thiserror::Error;

use super::mixer::MixedAudioFrame;

pub(crate) const SAMPLE_RATE_HZ: usize = 16_000;
/// Four seconds of canonical audio give local Faster-Whisper enough sentence context.
pub(crate) const CHUNK_SAMPLES: usize = 64_000;
/// One second of overlap protects speech that crosses a chunk boundary.
pub(crate) const OVERLAP_SAMPLES: usize = 16_000;
pub(crate) const HOP_SAMPLES: usize = CHUNK_SAMPLES - OVERLAP_SAMPLES;
pub(crate) const OVERLAP_SECONDS: f64 = 1.0;
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
    #[error("The mixed audio frame is invalid.")]
    InvalidFrame,
    #[error("The mixed audio timeline regressed.")]
    TimelineRegression,
}

pub(crate) struct AudioChunker {
    samples: Vec<f32>,
    start_time: Option<f64>,
    expected_next_time: Option<f64>,
    sequence: u64,
    /// Canonical sample offsets within the current contiguous timeline epoch.
    /// They make the final partial chunk's already-covered overlap explicit,
    /// rather than inferring coverage from the retained buffer length.
    pending_start_sample: u64,
    next_sample: u64,
    covered_until_sample: u64,
    has_emitted_full_chunk: bool,
}

impl Default for AudioChunker {
    fn default() -> Self {
        Self {
            samples: Vec::new(),
            start_time: None,
            expected_next_time: None,
            sequence: 0,
            pending_start_sample: 0,
            next_sample: 0,
            covered_until_sample: 0,
            has_emitted_full_chunk: false,
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
            return Err(AudioChunkerError::InvalidFrame);
        }
        let mut gap_detected = false;
        if let Some(expected) = self.expected_next_time {
            let delta = frame.capture_time_seconds - expected;
            if delta < -SAMPLE_SECONDS {
                return Err(AudioChunkerError::TimelineRegression);
            }
            if delta.abs() > GAP_TOLERANCE_SECONDS {
                self.clear_pending_timeline();
                gap_detected = true;
            }
        }
        if self.samples.is_empty() {
            self.start_time = Some(frame.capture_time_seconds);
        }
        self.expected_next_time =
            Some(frame.capture_time_seconds + frame.samples.len() as f64 * SAMPLE_SECONDS);
        self.next_sample = self
            .next_sample
            .saturating_add(u64::try_from(frame.samples.len()).expect("sample count fits in u64"));
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
                overlap_seconds: OVERLAP_SECONDS,
            });
            self.sequence += 1;
            self.has_emitted_full_chunk = true;
            self.covered_until_sample = self
                .covered_until_sample
                .max(self.pending_start_sample + CHUNK_SAMPLES as u64);
            self.samples.drain(..HOP_SAMPLES);
            self.pending_start_sample += HOP_SAMPLES as u64;
            self.start_time = Some(timestamp + HOP_SAMPLES as f64 * SAMPLE_SECONDS);
        }
        chunks
    }

    /// Emits the final live-STT request without padding or duplicating fully
    /// covered audio. After a full rolling chunk, the retained one-second
    /// overlap is included only when new tail samples exist beyond coverage.
    pub(crate) fn flush_final(&mut self) -> Option<AudioChunk> {
        if self.samples.is_empty() || self.next_sample <= self.covered_until_sample {
            return None;
        }

        let start_sample = if self.has_emitted_full_chunk {
            self.pending_start_sample.max(
                self.covered_until_sample
                    .saturating_sub(OVERLAP_SAMPLES as u64),
            )
        } else {
            self.pending_start_sample
        };
        let start_offset = usize::try_from(start_sample - self.pending_start_sample)
            .expect("final chunk offset fits in usize");
        let timestamp = self.start_time.expect("buffer start time is set")
            + start_offset as f64 * SAMPLE_SECONDS;
        let samples = self.samples[start_offset..].to_vec();
        let overlap_samples = self.covered_until_sample.saturating_sub(start_sample);
        let chunk = AudioChunk {
            sequence: self.sequence,
            capture_started_at_seconds: timestamp,
            samples,
            overlap_seconds: overlap_samples as f64 * SAMPLE_SECONDS,
        };
        self.sequence += 1;
        self.covered_until_sample = self.next_sample;
        self.clear_pending_timeline();
        Some(chunk)
    }

    pub(crate) fn reset(&mut self) {
        self.clear_pending_timeline();
        self.sequence = 0;
    }

    fn clear_pending_timeline(&mut self) {
        self.samples.clear();
        self.start_time = None;
        self.expected_next_time = None;
        self.pending_start_sample = 0;
        self.next_sample = 0;
        self.covered_until_sample = 0;
        self.has_emitted_full_chunk = false;
    }

    #[cfg(debug_assertions)]
    pub(crate) const fn expected_next_time_for_diagnostics(&self) -> Option<f64> {
        self.expected_next_time
    }
}

#[cfg(test)]
mod tests {
    use super::{
        AudioChunker, AudioChunkerError, CHUNK_SAMPLES, HOP_SAMPLES, OVERLAP_SAMPLES,
        OVERLAP_SECONDS, SAMPLE_RATE_HZ,
    };
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
        assert_eq!(first.overlap_seconds, OVERLAP_SECONDS);
        let second = chunker
            .push(frame(4.0, HOP_SAMPLES, CHUNK_SAMPLES))
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
            Err(AudioChunkerError::TimelineRegression)
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

    fn push_duration(chunker: &mut AudioChunker, sample_count: usize) {
        let mut offset = 0;
        while offset < sample_count {
            let count = (sample_count - offset).min(997);
            let timestamp = offset as f64 / SAMPLE_RATE_HZ as f64;
            chunker.push(frame(timestamp, count, offset)).expect("push");
            offset += count;
        }
    }

    fn assert_final_tail(total_samples: usize, expected: Option<(usize, usize, f64)>) {
        let mut chunker = AudioChunker::default();
        push_duration(&mut chunker, total_samples);
        let full_chunks = chunker.flush_complete_chunks();
        assert!(full_chunks.is_empty(), "push drains every full chunk");

        match expected {
            Some((start, count, overlap_seconds)) => {
                let final_chunk = chunker.flush_final().expect("final tail");
                assert_eq!(final_chunk.samples.len(), count);
                assert_eq!(
                    final_chunk.samples,
                    (start..start + count)
                        .map(|sample| sample as f32)
                        .collect::<Vec<_>>()
                );
                assert_eq!(
                    final_chunk.capture_started_at_seconds,
                    start as f64 / SAMPLE_RATE_HZ as f64
                );
                assert_eq!(final_chunk.overlap_seconds, overlap_seconds);
                assert!(chunker.flush_final().is_none(), "final tail is idempotent");
            }
            None => assert!(
                chunker.flush_final().is_none(),
                "exact coverage has no tail"
            ),
        }
    }

    #[test]
    fn flush_final_preserves_only_uncovered_live_tail_intervals() {
        assert_final_tail(SAMPLE_RATE_HZ, Some((0, SAMPLE_RATE_HZ, 0.0)));
        assert_final_tail(
            SAMPLE_RATE_HZ * 5 / 2,
            Some((0, SAMPLE_RATE_HZ * 5 / 2, 0.0)),
        );
        assert_final_tail(
            SAMPLE_RATE_HZ * 39 / 10,
            Some((0, SAMPLE_RATE_HZ * 39 / 10, 0.0)),
        );
        assert_final_tail(CHUNK_SAMPLES, None);
        assert_final_tail(
            CHUNK_SAMPLES + 1,
            Some((HOP_SAMPLES, OVERLAP_SAMPLES + 1, 1.0)),
        );
        assert_final_tail(
            SAMPLE_RATE_HZ * 69 / 10,
            Some((HOP_SAMPLES, SAMPLE_RATE_HZ * 39 / 10, 1.0)),
        );
        assert_final_tail(CHUNK_SAMPLES + HOP_SAMPLES, None);
        assert_final_tail(SAMPLE_RATE_HZ * 43, None);
        assert_final_tail(
            SAMPLE_RATE_HZ * 43 + 1,
            Some((SAMPLE_RATE_HZ * 42, SAMPLE_RATE_HZ + 1, 1.0)),
        );
        assert_final_tail(
            SAMPLE_RATE_HZ * 45,
            Some((SAMPLE_RATE_HZ * 42, SAMPLE_RATE_HZ * 3, 1.0)),
        );
    }
}
