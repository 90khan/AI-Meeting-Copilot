//! Independent five-second, zero-overlap chunks for retained recordings.

use std::fmt;

use thiserror::Error;

use super::chunker::SAMPLE_RATE_HZ;

pub(crate) const RECORDING_CHUNK_SAMPLES: usize = SAMPLE_RATE_HZ * 5;
const SAMPLE_SECONDS: f64 = 1.0 / SAMPLE_RATE_HZ as f64;
const GAP_TOLERANCE_SECONDS: f64 = 0.100;

pub(crate) struct RecordingAudioChunk {
    pub(crate) segment_index: u32,
    pub(crate) samples: Vec<f32>,
    pub(crate) sample_count: usize,
    pub(crate) first_sample_timestamp_seconds: f64,
}

impl fmt::Debug for RecordingAudioChunk {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RecordingAudioChunk")
            .field("segment_index", &self.segment_index)
            .field("samples", &"<redacted>")
            .field("sample_count", &self.sample_count)
            .field(
                "first_sample_timestamp_seconds",
                &self.first_sample_timestamp_seconds,
            )
            .finish()
    }
}

pub(crate) struct RecordingChunkingResult {
    pub(crate) chunks: Vec<RecordingAudioChunk>,
    pub(crate) gap_detected: bool,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum RecordingChunkerError {
    #[error("The recording audio timeline is invalid.")]
    InvalidTimeline,
}

/// Retains exact canonical samples in independent five-second WAV segments.
pub(crate) struct RecordingAudioChunker {
    samples: Vec<f32>,
    start_time: Option<f64>,
    expected_next_time: Option<f64>,
    next_segment_index: u32,
}

impl Default for RecordingAudioChunker {
    fn default() -> Self {
        Self {
            samples: Vec::new(),
            start_time: None,
            expected_next_time: None,
            next_segment_index: 0,
        }
    }
}

impl RecordingAudioChunker {
    /// Adds one canonical frame. A detected gap flushes the prior partial chunk
    /// and starts a new timeline; no silence is invented across that gap.
    pub(crate) fn push(
        &mut self,
        capture_time_seconds: f64,
        samples: &[f32],
    ) -> Result<RecordingChunkingResult, RecordingChunkerError> {
        if !capture_time_seconds.is_finite() || capture_time_seconds < 0.0 || samples.is_empty() {
            return Err(RecordingChunkerError::InvalidTimeline);
        }
        let mut chunks = Vec::new();
        let mut gap_detected = false;
        if let Some(expected) = self.expected_next_time {
            let delta = capture_time_seconds - expected;
            if delta < -SAMPLE_SECONDS {
                return Err(RecordingChunkerError::InvalidTimeline);
            }
            if delta.abs() > GAP_TOLERANCE_SECONDS {
                if let Some(chunk) = self.flush_final() {
                    chunks.push(chunk);
                }
                self.expected_next_time = None;
                gap_detected = true;
            }
        }
        if self.samples.is_empty() {
            self.start_time = Some(capture_time_seconds);
        }
        self.expected_next_time =
            Some(capture_time_seconds + samples.len() as f64 * SAMPLE_SECONDS);
        self.samples.extend_from_slice(samples);
        chunks.extend(self.flush_complete());
        Ok(RecordingChunkingResult {
            chunks,
            gap_detected,
        })
    }

    pub(crate) fn flush_final(&mut self) -> Option<RecordingAudioChunk> {
        if self.samples.is_empty() {
            return None;
        }
        let samples = std::mem::take(&mut self.samples);
        let chunk = RecordingAudioChunk {
            segment_index: self.next_segment_index,
            sample_count: samples.len(),
            samples,
            first_sample_timestamp_seconds: self.start_time.take().expect("start time"),
        };
        self.next_segment_index = self.next_segment_index.saturating_add(1);
        self.expected_next_time = None;
        Some(chunk)
    }

    pub(crate) fn reset(&mut self) {
        self.samples.clear();
        self.start_time = None;
        self.expected_next_time = None;
        self.next_segment_index = 0;
    }

    fn flush_complete(&mut self) -> Vec<RecordingAudioChunk> {
        let mut chunks = Vec::new();
        while self.samples.len() >= RECORDING_CHUNK_SAMPLES {
            let samples: Vec<f32> = self.samples.drain(..RECORDING_CHUNK_SAMPLES).collect();
            let timestamp = self.start_time.expect("start time");
            chunks.push(RecordingAudioChunk {
                segment_index: self.next_segment_index,
                sample_count: samples.len(),
                samples,
                first_sample_timestamp_seconds: timestamp,
            });
            self.next_segment_index = self.next_segment_index.saturating_add(1);
            self.start_time = Some(timestamp + RECORDING_CHUNK_SAMPLES as f64 * SAMPLE_SECONDS);
        }
        chunks
    }
}

#[cfg(test)]
mod tests {
    use super::{RecordingAudioChunker, RECORDING_CHUNK_SAMPLES};

    #[test]
    fn chunks_without_overlap_or_padding_and_flushes_final_short_segment() {
        let mut chunker = RecordingAudioChunker::default();
        let input: Vec<f32> = (0..(RECORDING_CHUNK_SAMPLES * 2 + 1))
            .map(|value| value as f32)
            .collect();
        let result = chunker.push(0.0, &input).expect("push");
        assert_eq!(result.chunks.len(), 2);
        let final_chunk = chunker.flush_final().expect("short final chunk");
        assert_eq!(final_chunk.segment_index, 2);
        assert_eq!(
            final_chunk.samples,
            vec![(RECORDING_CHUNK_SAMPLES * 2) as f32]
        );
        assert!(chunker.flush_final().is_none());
        let reconstructed: Vec<f32> = result
            .chunks
            .into_iter()
            .flat_map(|chunk| chunk.samples)
            .chain(final_chunk.samples)
            .collect();
        assert_eq!(reconstructed, input);
    }

    #[test]
    fn emits_exact_full_segment_counts_and_monotonic_indexes() {
        let mut chunker = RecordingAudioChunker::default();
        let one = vec![0.0; RECORDING_CHUNK_SAMPLES];
        let result = chunker.push(0.0, &one).expect("one segment");
        assert_eq!(result.chunks.len(), 1);
        assert_eq!(result.chunks[0].segment_index, 0);
        assert_eq!(result.chunks[0].sample_count, RECORDING_CHUNK_SAMPLES);

        let two = vec![0.0; RECORDING_CHUNK_SAMPLES];
        let result = chunker.push(5.0, &two).expect("second segment");
        assert_eq!(result.chunks.len(), 1);
        assert_eq!(result.chunks[0].segment_index, 1);
        assert!(chunker.flush_final().is_none());
    }

    #[test]
    fn gap_flushes_existing_samples_without_bridging_timelines() {
        let mut chunker = RecordingAudioChunker::default();
        let first = vec![1.0; 10];
        let second = vec![2.0; 10];
        assert!(chunker.push(0.0, &first).expect("first").chunks.is_empty());
        let result = chunker.push(1.0, &second).expect("gap");
        assert!(result.gap_detected);
        assert_eq!(result.chunks[0].samples, first);
        assert_eq!(chunker.flush_final().expect("second").samples, second);
    }
}
