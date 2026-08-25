//! Bounded PTS reordering for concurrently delivered native audio sources.
//!
//! ScreenCaptureKit can invoke the system-audio and microphone output queues in
//! a different order from their CoreMedia presentation timestamps. This stage
//! is enabled only when both sources are requested. It preserves every source
//! PTS and provides a short, bounded ordering window before DSP and mixing.

use super::types::NativeAudioFrame;

/// Covers the observed roughly 32 ms cross-source callback skew while keeping
/// added mixed-capture latency below one normal 10 ms callback batch plus the
/// explicitly bounded holdback.
pub(crate) const CROSS_SOURCE_PTS_HOLDBACK_SECONDS: f64 = 0.050;

/// At approximately 10 ms callbacks from two sources, this holds at most about
/// 80 ms of native frames. Reaching the limit forces the oldest PTS onward;
/// frames are never dropped or timestamp-clamped by this stage.
pub(crate) const CROSS_SOURCE_REORDER_CAPACITY: usize = 16;

pub(crate) struct OrderedNativeFrame {
    pub(crate) frame: NativeAudioFrame,
    pub(crate) arrival_sequence: u64,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct ReorderMetrics {
    pub(crate) reordered_frame_count: u64,
    pub(crate) max_observed_skew_microseconds: u64,
    pub(crate) high_water_mark: usize,
    pub(crate) forced_release_count: u64,
    pub(crate) shutdown_flush_count: u64,
}

struct BufferedNativeFrame {
    frame: NativeAudioFrame,
    arrival_sequence: u64,
}

/// Stable, capacity-bounded reordering. Equal PTS values preserve their input
/// arrival order, and single-source capture takes the direct no-holdback path.
pub(crate) struct NativeFrameReorderBuffer {
    enabled: bool,
    buffered: Vec<BufferedNativeFrame>,
    next_arrival_sequence: u64,
    latest_timestamp: Option<f64>,
    metrics: ReorderMetrics,
}

impl NativeFrameReorderBuffer {
    pub(crate) const fn new(enabled: bool) -> Self {
        Self {
            enabled,
            buffered: Vec::new(),
            next_arrival_sequence: 1,
            latest_timestamp: None,
            metrics: ReorderMetrics {
                reordered_frame_count: 0,
                max_observed_skew_microseconds: 0,
                high_water_mark: 0,
                forced_release_count: 0,
                shutdown_flush_count: 0,
            },
        }
    }

    pub(crate) fn push(&mut self, frame: NativeAudioFrame) -> Vec<OrderedNativeFrame> {
        let arrival_sequence = self.next_arrival_sequence;
        self.next_arrival_sequence = self.next_arrival_sequence.saturating_add(1);
        if !self.enabled {
            return vec![OrderedNativeFrame {
                frame,
                arrival_sequence,
            }];
        }

        let timestamp = frame.capture_time_seconds();
        if let Some(latest_timestamp) = self.latest_timestamp {
            let skew_seconds = (latest_timestamp - timestamp).max(0.0);
            self.metrics.max_observed_skew_microseconds = self
                .metrics
                .max_observed_skew_microseconds
                .max(seconds_to_microseconds(skew_seconds));
        }
        self.latest_timestamp = Some(
            self.latest_timestamp
                .map_or(timestamp, |latest| latest.max(timestamp)),
        );

        let mut released = Vec::new();
        if self.buffered.len() == CROSS_SOURCE_REORDER_CAPACITY {
            self.metrics.forced_release_count = self.metrics.forced_release_count.saturating_add(1);
            let oldest_timestamp = self
                .buffered
                .first()
                .expect("full buffer has an oldest frame")
                .frame
                .capture_time_seconds();
            if timestamp < oldest_timestamp {
                // The just-arrived frame is globally oldest. Release it now
                // rather than exceed capacity or rewrite its PTS.
                self.metrics.reordered_frame_count =
                    self.metrics.reordered_frame_count.saturating_add(1);
                released.push(OrderedNativeFrame {
                    frame,
                    arrival_sequence,
                });
            } else {
                released.push(self.release_oldest().expect("full buffer has a frame"));
                self.insert(frame, arrival_sequence);
            }
        } else {
            self.insert(frame, arrival_sequence);
        }
        self.metrics.high_water_mark = self.metrics.high_water_mark.max(self.buffered.len());

        let release_before = self.latest_timestamp.expect("latest timestamp is recorded")
            - CROSS_SOURCE_PTS_HOLDBACK_SECONDS;
        released.extend(self.release_through(release_before));
        released
    }

    /// Releases all retained PTS-ordered frames while capture stops.
    pub(crate) fn flush(&mut self) -> Vec<OrderedNativeFrame> {
        if !self.enabled {
            return Vec::new();
        }
        let count = u64::try_from(self.buffered.len()).unwrap_or(u64::MAX);
        self.metrics.shutdown_flush_count = self.metrics.shutdown_flush_count.saturating_add(count);
        self.buffered
            .drain(..)
            .map(|frame| OrderedNativeFrame {
                frame: frame.frame,
                arrival_sequence: frame.arrival_sequence,
            })
            .collect()
    }

    pub(crate) const fn metrics(&self) -> ReorderMetrics {
        self.metrics
    }

    fn release_through(&mut self, timestamp: f64) -> Vec<OrderedNativeFrame> {
        let mut released = Vec::new();
        while self
            .buffered
            .first()
            .is_some_and(|frame| frame.frame.capture_time_seconds() <= timestamp)
        {
            released.push(self.release_oldest().expect("first frame exists"));
        }
        released
    }

    fn insert(&mut self, frame: NativeAudioFrame, arrival_sequence: u64) {
        let timestamp = frame.capture_time_seconds();
        let insertion_index = self.buffered.partition_point(|existing| {
            let ordering = existing.frame.capture_time_seconds().total_cmp(&timestamp);
            ordering.is_lt() || (ordering.is_eq() && existing.arrival_sequence < arrival_sequence)
        });
        if insertion_index != self.buffered.len() {
            self.metrics.reordered_frame_count =
                self.metrics.reordered_frame_count.saturating_add(1);
        }
        self.buffered.insert(
            insertion_index,
            BufferedNativeFrame {
                frame,
                arrival_sequence,
            },
        );
    }

    fn release_oldest(&mut self) -> Option<OrderedNativeFrame> {
        if self.buffered.is_empty() {
            return None;
        }
        let frame = self.buffered.remove(0);
        Some(OrderedNativeFrame {
            frame: frame.frame,
            arrival_sequence: frame.arrival_sequence,
        })
    }
}

fn seconds_to_microseconds(seconds: f64) -> u64 {
    u64::try_from((seconds * 1_000_000.0) as u128).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::{NativeFrameReorderBuffer, CROSS_SOURCE_REORDER_CAPACITY};
    use crate::audio_capture::types::{
        NativeAudioFormat, NativeAudioFrame, NativeAudioSamples, NativeAudioSource,
        NativeSampleFormat,
    };

    fn frame(source: NativeAudioSource, timestamp: f64) -> NativeAudioFrame {
        NativeAudioFrame::new(
            source,
            NativeAudioFormat::new(16_000, 1, NativeSampleFormat::Float32, true)
                .expect("valid format"),
            timestamp,
            NativeAudioSamples::Float32(vec![0.0; 160]),
        )
        .expect("valid frame")
    }

    fn timestamps(frames: Vec<super::OrderedNativeFrame>) -> Vec<f64> {
        frames
            .into_iter()
            .map(|frame| frame.frame.capture_time_seconds())
            .collect()
    }

    #[test]
    fn reorders_an_older_system_frame_after_a_microphone_frame() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        assert!(reorder
            .push(frame(NativeAudioSource::Microphone, 1.032))
            .is_empty());
        assert!(reorder
            .push(frame(NativeAudioSource::SystemAudio, 1.000))
            .is_empty());

        assert_eq!(timestamps(reorder.flush()), vec![1.000, 1.032]);
        assert_eq!(reorder.metrics().reordered_frame_count, 1);
        assert_eq!(reorder.metrics().max_observed_skew_microseconds, 32_000);
    }

    #[test]
    fn reorders_an_older_microphone_frame_after_a_system_frame() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.032));
        reorder.push(frame(NativeAudioSource::Microphone, 1.000));

        assert_eq!(timestamps(reorder.flush()), vec![1.000, 1.032]);
        assert_eq!(reorder.metrics().reordered_frame_count, 1);
    }

    #[test]
    fn preserves_frames_already_in_timestamp_order() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.0));
        reorder.push(frame(NativeAudioSource::Microphone, 1.01));
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.02));

        let frames = reorder.flush();
        assert_eq!(timestamps(frames), vec![1.0, 1.01, 1.02]);
        assert_eq!(reorder.metrics().reordered_frame_count, 0);
    }

    #[test]
    fn preserves_arrival_order_for_equal_timestamps() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.0));
        reorder.push(frame(NativeAudioSource::Microphone, 1.0));

        let frames = reorder.flush();
        assert_eq!(timestamps(frames), vec![1.0, 1.0]);
        let mut reorder = NativeFrameReorderBuffer::new(true);
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.0));
        reorder.push(frame(NativeAudioSource::Microphone, 1.0));
        assert_eq!(
            reorder
                .flush()
                .into_iter()
                .map(|frame| frame.frame.source())
                .collect::<Vec<_>>(),
            vec![
                NativeAudioSource::SystemAudio,
                NativeAudioSource::Microphone
            ]
        );
    }

    #[test]
    fn single_source_paths_release_directly_without_holdback() {
        for source in [
            NativeAudioSource::SystemAudio,
            NativeAudioSource::Microphone,
        ] {
            let mut reorder = NativeFrameReorderBuffer::new(false);
            assert_eq!(timestamps(reorder.push(frame(source, 1.0))), vec![1.0]);
            assert!(reorder.flush().is_empty());
            assert_eq!(reorder.metrics().high_water_mark, 0);
        }
    }

    #[test]
    fn holdback_releases_frames_once_the_newest_pts_is_far_enough_ahead() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.0));
        assert_eq!(
            timestamps(reorder.push(frame(NativeAudioSource::Microphone, 1.051))),
            vec![1.0]
        );
    }

    #[test]
    fn shutdown_flush_releases_every_remaining_pts_ordered_frame() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        reorder.push(frame(NativeAudioSource::Microphone, 1.032));
        reorder.push(frame(NativeAudioSource::SystemAudio, 1.000));

        assert_eq!(timestamps(reorder.flush()), vec![1.000, 1.032]);
        assert_eq!(reorder.metrics().shutdown_flush_count, 2);
    }

    #[test]
    fn capacity_forces_oldest_pts_onward_without_dropping_frames() {
        let mut reorder = NativeFrameReorderBuffer::new(true);
        let mut released = Vec::new();
        for offset in 0..=CROSS_SOURCE_REORDER_CAPACITY {
            released.extend(reorder.push(frame(
                NativeAudioSource::SystemAudio,
                1.0 + offset as f64 * 0.001,
            )));
        }

        assert_eq!(reorder.metrics().forced_release_count, 1);
        assert_eq!(
            reorder.metrics().high_water_mark,
            CROSS_SOURCE_REORDER_CAPACITY
        );
        released.extend(reorder.flush());
        assert_eq!(released.len(), CROSS_SOURCE_REORDER_CAPACITY + 1);
        assert_eq!(
            timestamps(released),
            (0..=CROSS_SOURCE_REORDER_CAPACITY)
                .map(|offset| 1.0 + offset as f64 * 0.001)
                .collect::<Vec<_>>()
        );
    }
}
