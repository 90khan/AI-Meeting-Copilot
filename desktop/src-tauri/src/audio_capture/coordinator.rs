//! Lifecycle holder for native capture, DSP, chunk encoding, and submission.

use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Instant,
};

use thiserror::Error;
use tokio::{
    sync::{mpsc, oneshot, Notify},
    task::JoinHandle,
    time::{timeout, Duration},
};

use super::{
    bridge::{NativeAudioCaptureBridge, NativeAudioFrameSender},
    chunker::{AudioChunker, AudioChunkerError},
    mixer::MixedAudioFrame,
    processing::AudioFrameProcessor,
    recording_chunker::RecordingAudioChunker,
    recording_writer::{EncodedRecordingSegment, RecordingFailureCode, RecordingSegmentWriter},
    reorder::{NativeFrameReorderBuffer, OrderedNativeFrame},
    resampler::AudioProcessingError,
    sender::{
        ChunkSenderTask, ChunkSubmitter, FinalizedChunkQueue, FinalizedChunkQueueError,
        GRACEFUL_SENDER_DRAIN_TIMEOUT,
    },
    status::{AudioCaptureState, AudioCaptureStatus},
    types::{AudioCaptureConfiguration, NativeAudioFrame},
    wav::{build_wav_from_samples, encode_audio_chunk},
};

#[cfg(debug_assertions)]
use super::types::NativeAudioSource;

const NATIVE_FRAME_BUFFER_CAPACITY: usize = 8;
const GAP_MESSAGE: &str = "An audio chunk was dropped because transcription fell behind.";
const SUBMISSION_FAILURE_MESSAGE: &str = "Audio submission failed.";
const RECORDING_UNAVAILABLE_MESSAGE: &str = "Recording is unavailable.";

/// A typical native callback carries about 10 ms of audio. A 50 ms synchronous
/// processing interval is therefore material relative to the eight-frame
/// callback buffer, while still avoiding per-frame diagnostic noise.
#[cfg(debug_assertions)]
const SLOW_PROCESSING_FRAME_THRESHOLD_MILLISECONDS: u128 = 50;
#[cfg(debug_assertions)]
const PROCESSING_THROUGHPUT_REPORT_INTERVAL: u64 = 500;

#[cfg(debug_assertions)]
#[derive(Default)]
struct ProcessingSourceMetrics {
    frame_count: u64,
    input_microseconds: u64,
}

#[cfg(debug_assertions)]
impl ProcessingSourceMetrics {
    fn record(&mut self, duration_microseconds: u64) {
        self.frame_count = self.frame_count.saturating_add(1);
        self.input_microseconds = self
            .input_microseconds
            .saturating_add(duration_microseconds);
    }
}

#[cfg(debug_assertions)]
struct ProcessingWorkerMetrics {
    started_at: Instant,
    completed_frame_count: u64,
    failed_frame_count: u64,
    total_processing_microseconds: u64,
    slow_frame_count: u64,
    system: ProcessingSourceMetrics,
    microphone: ProcessingSourceMetrics,
}

#[cfg(debug_assertions)]
impl ProcessingWorkerMetrics {
    fn new() -> Self {
        Self {
            started_at: Instant::now(),
            completed_frame_count: 0,
            failed_frame_count: 0,
            total_processing_microseconds: 0,
            slow_frame_count: 0,
            system: ProcessingSourceMetrics::default(),
            microphone: ProcessingSourceMetrics::default(),
        }
    }

    fn source_metrics(&mut self, source: NativeAudioSource) -> &mut ProcessingSourceMetrics {
        match source {
            NativeAudioSource::SystemAudio => &mut self.system,
            NativeAudioSource::Microphone => &mut self.microphone,
        }
    }

    fn record_frame(
        &mut self,
        source: NativeAudioSource,
        duration_microseconds: u64,
        processing_elapsed: std::time::Duration,
        succeeded: bool,
    ) -> bool {
        self.source_metrics(source).record(duration_microseconds);
        self.total_processing_microseconds = self
            .total_processing_microseconds
            .saturating_add(duration_to_microseconds(processing_elapsed));
        if succeeded {
            self.completed_frame_count = self.completed_frame_count.saturating_add(1);
        } else {
            self.failed_frame_count = self.failed_frame_count.saturating_add(1);
        }
        if processing_elapsed.as_millis() < SLOW_PROCESSING_FRAME_THRESHOLD_MILLISECONDS {
            return false;
        }
        self.slow_frame_count = self.slow_frame_count.saturating_add(1);
        self.slow_frame_count == 1 || self.slow_frame_count % 25 == 0
    }

    fn report_if_due(&self, generation: u64, processed_frame_count: u64) {
        if processed_frame_count % PROCESSING_THROUGHPUT_REPORT_INTERVAL != 0 {
            return;
        }
        eprintln!(
            "audio-capture processing throughput generation={generation} elapsed_ms={} received_frame_count={processed_frame_count} completed_frame_count={} failed_frame_count={} system_input_ms={} system_frame_count={} microphone_input_ms={} microphone_frame_count={} processing_total_ms={} slow_frame_count={}",
            self.started_at.elapsed().as_millis(),
            self.completed_frame_count,
            self.failed_frame_count,
            self.system.input_microseconds / 1_000,
            self.system.frame_count,
            self.microphone.input_microseconds / 1_000,
            self.microphone.frame_count,
            self.total_processing_microseconds / 1_000,
            self.slow_frame_count,
        );
    }
}

#[cfg(debug_assertions)]
fn duration_to_microseconds(duration: std::time::Duration) -> u64 {
    u64::try_from(duration.as_micros()).unwrap_or(u64::MAX)
}

#[cfg(debug_assertions)]
struct FinalizedChunkMetrics {
    started_at: Instant,
    last_finalized_at: Option<Instant>,
    finalized_count: u64,
}

#[cfg(debug_assertions)]
impl Default for FinalizedChunkMetrics {
    fn default() -> Self {
        Self {
            started_at: Instant::now(),
            last_finalized_at: None,
            finalized_count: 0,
        }
    }
}

#[cfg(debug_assertions)]
impl FinalizedChunkMetrics {
    fn reset(&mut self) {
        self.started_at = Instant::now();
        self.last_finalized_at = None;
        self.finalized_count = 0;
    }

    fn note_finalized(&mut self) -> (u64, u128, u128) {
        let now = Instant::now();
        let cadence_ms = self
            .last_finalized_at
            .map(|previous| now.duration_since(previous).as_millis())
            .unwrap_or(0);
        self.last_finalized_at = Some(now);
        self.finalized_count = self.finalized_count.saturating_add(1);
        (
            self.finalized_count,
            cadence_ms,
            now.duration_since(self.started_at).as_millis(),
        )
    }
}

struct ProcessingPipeline {
    processor: AudioFrameProcessor,
    chunker: AudioChunker,
    submission_gap: bool,
    recording: Option<RecordingBranch>,
    recording_unavailable: bool,
    #[cfg(debug_assertions)]
    finalized_chunk_metrics: FinalizedChunkMetrics,
    #[cfg(debug_assertions)]
    timeline_diagnostics: TimelineDiagnostics,
}

#[cfg(debug_assertions)]
#[derive(Default)]
struct TimelineDiagnostics {
    generation: u64,
    processing_arrival_sequence: u64,
    previous_source: Option<NativeAudioSource>,
    previous_accepted_timestamp: Option<f64>,
    last_regression: Option<TimelineRegressionDiagnostic>,
    regression_reported: bool,
}

#[cfg(debug_assertions)]
#[derive(Clone, Copy)]
struct TimelineRegressionDiagnostic {
    source: NativeAudioSource,
    incoming_timestamp: f64,
    chunker_timestamp: f64,
    previous_source: Option<NativeAudioSource>,
    previous_accepted_timestamp: Option<f64>,
    previous_expected_timestamp: Option<f64>,
    frame_duration_microseconds: u64,
    sample_rate_hz: u32,
    channels: u16,
    sample_format: super::types::NativeSampleFormat,
    interleaved: bool,
    processing_arrival_sequence: u64,
    generation: u64,
}

struct RecordingBranch {
    chunker: RecordingAudioChunker,
    writer: Arc<dyn RecordingSegmentWriter>,
}

impl Default for ProcessingPipeline {
    fn default() -> Self {
        Self {
            processor: AudioFrameProcessor::default(),
            chunker: AudioChunker::default(),
            submission_gap: false,
            recording: None,
            recording_unavailable: false,
            #[cfg(debug_assertions)]
            finalized_chunk_metrics: FinalizedChunkMetrics::default(),
            #[cfg(debug_assertions)]
            timeline_diagnostics: TimelineDiagnostics::default(),
        }
    }
}

impl ProcessingPipeline {
    fn reset(&mut self) {
        self.processor.reset();
        self.chunker.reset();
        self.submission_gap = false;
        self.recording = None;
        self.recording_unavailable = false;
        #[cfg(debug_assertions)]
        self.finalized_chunk_metrics.reset();
        #[cfg(debug_assertions)]
        {
            self.timeline_diagnostics = TimelineDiagnostics::default();
        }
    }
}

pub(crate) struct AudioCaptureCoordinator<B: NativeAudioCaptureBridge> {
    bridge: B,
    pipeline: Arc<Mutex<ProcessingPipeline>>,
    processing_task: Option<JoinHandle<()>>,
    processing_failed: Arc<AtomicBool>,
    final_tail_admission_failed: Arc<AtomicBool>,
    stopping: Arc<AtomicBool>,
    graceful_stopping: Arc<AtomicBool>,
    finalized_queue: Option<Arc<FinalizedChunkQueue>>,
    sender_task: Option<ChunkSenderTask>,
    state: AudioCaptureState,
}

impl<B: NativeAudioCaptureBridge> AudioCaptureCoordinator<B> {
    pub(crate) fn new(bridge: B) -> Self {
        Self {
            bridge,
            pipeline: Arc::new(Mutex::new(ProcessingPipeline::default())),
            processing_task: None,
            processing_failed: Arc::new(AtomicBool::new(false)),
            final_tail_admission_failed: Arc::new(AtomicBool::new(false)),
            stopping: Arc::new(AtomicBool::new(false)),
            graceful_stopping: Arc::new(AtomicBool::new(false)),
            finalized_queue: None,
            sender_task: None,
            state: AudioCaptureState::Stopped,
        }
    }

    /// Administrative operations are intentionally exposed only to the Rust
    /// command boundary; audio callbacks never use this access path.
    pub(crate) fn bridge_mut(&mut self) -> &mut B {
        &mut self.bridge
    }

    /// Starts capture only after a submission boundary is supplied.
    pub(crate) async fn start(
        &mut self,
        configuration: &AudioCaptureConfiguration,
        submitter: Arc<dyn ChunkSubmitter>,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        self.start_with_recording(configuration, submitter, None, false)
            .await
    }

    /// Starts transcription and, when prepared, an independent recording branch.
    pub(crate) async fn start_with_recording(
        &mut self,
        configuration: &AudioCaptureConfiguration,
        submitter: Arc<dyn ChunkSubmitter>,
        recording_writer: Option<Arc<dyn RecordingSegmentWriter>>,
        recording_unavailable: bool,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if !matches!(
            self.state,
            AudioCaptureState::Stopped | AudioCaptureState::Failed
        ) || self.sender_task.is_some()
            || self.processing_task.is_some()
        {
            return Err(AudioCaptureCoordinatorError::AlreadyStarted);
        }

        self.state = AudioCaptureState::Starting;
        self.processing_failed.store(false, Ordering::Release);
        self.final_tail_admission_failed
            .store(false, Ordering::Release);
        self.stopping.store(false, Ordering::Release);
        self.graceful_stopping.store(false, Ordering::Release);
        {
            let mut pipeline = self.pipeline.lock().expect("processing pipeline lock");
            pipeline.reset();
            pipeline.processor.set_common_timeline_mixing(
                configuration.include_system_audio() && configuration.include_microphone(),
            );
            pipeline.recording = recording_writer.map(|writer| RecordingBranch {
                chunker: RecordingAudioChunker::default(),
                writer,
            });
            pipeline.recording_unavailable = recording_unavailable;
        }
        let (sender_task, finalized_queue) = ChunkSenderTask::start(submitter);
        let (sender, receiver) = mpsc::channel(NATIVE_FRAME_BUFFER_CAPACITY);

        let frame_sender = NativeAudioFrameSender::new(sender);
        #[cfg(debug_assertions)]
        let generation = frame_sender.generation();
        let sender_failed = sender_task.failure_signal();
        let sender_failure_notify = sender_task.failure_notifier();
        let (worker_ready_sender, worker_ready_receiver) = oneshot::channel();
        let processing_task = tokio::spawn(run_processing_worker(
            receiver,
            configuration.include_system_audio() && configuration.include_microphone(),
            Arc::clone(&self.pipeline),
            Arc::clone(&finalized_queue),
            Arc::clone(&self.processing_failed),
            Arc::clone(&self.final_tail_admission_failed),
            Arc::clone(&self.stopping),
            Arc::clone(&self.graceful_stopping),
            sender_failed,
            sender_failure_notify,
            worker_ready_sender,
            #[cfg(debug_assertions)]
            generation,
        ));
        #[cfg(debug_assertions)]
        eprintln!("audio-capture processing worker spawned");
        if worker_ready_receiver.await.is_err() {
            #[cfg(debug_assertions)]
            eprintln!("audio-capture processing worker aborted reason=readiness_failed");
            processing_task.abort();
            let _ = processing_task.await;
            sender_task.abort().await;
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StartFailed);
        }
        #[cfg(debug_assertions)]
        eprintln!("audio-capture processing worker readiness acknowledged");

        if self.bridge.start(configuration, frame_sender).is_err() {
            #[cfg(debug_assertions)]
            eprintln!("audio-capture processing worker aborted reason=native_start_failed");
            processing_task.abort();
            let _ = processing_task.await;
            sender_task.abort().await;
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StartFailed);
        }
        #[cfg(debug_assertions)]
        eprintln!("audio-capture native start succeeded");

        self.finalized_queue = Some(finalized_queue);
        self.sender_task = Some(sender_task);
        self.processing_task = Some(processing_task);
        #[cfg(debug_assertions)]
        eprintln!("audio-capture processing worker handle stored");
        self.state = AudioCaptureState::Capturing;
        #[cfg(debug_assertions)]
        eprintln!("audio-capture capture state committed");
        self.status()
    }

    /// Gracefully completes normal user stop: native input drains into the
    /// existing FIFO, an eligible final live-STT tail is admitted once, and
    /// the FIFO drains under one bounded deadline.
    pub(crate) async fn stop(
        &mut self,
    ) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        if self.state == AudioCaptureState::Stopped && self.sender_task.is_none() {
            return self.status();
        }
        if self.state == AudioCaptureState::Failed {
            self.abort().await;
            return self.status();
        }

        self.state = AudioCaptureState::Stopping;
        self.stopping.store(true, Ordering::Release);
        self.graceful_stopping.store(true, Ordering::Release);
        let graceful_stop_started_at = Instant::now();
        let native_stop = self.bridge.stop();
        if native_stop.is_err() {
            self.abort_processing_and_sender().await;
            finish_recording_finalization(finalize_recording_branch(&self.pipeline)).await;
            self.pipeline
                .lock()
                .expect("processing pipeline lock")
                .reset();
            self.state = AudioCaptureState::Failed;
            return Err(AudioCaptureCoordinatorError::StopFailed);
        }
        if let Some(mut task) = self.processing_task.take() {
            if timeout(
                remaining_graceful_stop_time(graceful_stop_started_at),
                &mut task,
            )
            .await
            .is_err()
            {
                #[cfg(debug_assertions)]
                eprintln!("audio-capture processing worker aborted reason=stop_timeout");
                task.abort();
                let _ = task.await;
                self.abort_sender_task().await;
                finish_recording_finalization(finalize_recording_branch(&self.pipeline)).await;
                self.pipeline
                    .lock()
                    .expect("processing pipeline lock")
                    .reset();
                self.state = AudioCaptureState::Failed;
                return Err(AudioCaptureCoordinatorError::GracefulFinishFailed);
            }
        }

        let processing_failed = self.processing_failed.load(Ordering::Acquire);
        let final_tail_admission_failed = self.final_tail_admission_failed.load(Ordering::Acquire);
        if let Some(sender_task) = self.sender_task.take() {
            let sender_result = if processing_failed && !final_tail_admission_failed {
                sender_task.abort().await;
                Err(())
            } else {
                sender_task
                    .finish_gracefully(remaining_graceful_stop_time(graceful_stop_started_at))
                    .await
                    .map_err(|_| ())
            };
            self.finalized_queue.take();
            finish_recording_finalization(finalize_recording_branch(&self.pipeline)).await;
            self.pipeline
                .lock()
                .expect("processing pipeline lock")
                .reset();
            if sender_result.is_err() || final_tail_admission_failed {
                self.state = AudioCaptureState::Failed;
                return Err(AudioCaptureCoordinatorError::GracefulFinishFailed);
            }
        } else {
            self.finalized_queue.take();
            finish_recording_finalization(finalize_recording_branch(&self.pipeline)).await;
            self.pipeline
                .lock()
                .expect("processing pipeline lock")
                .reset();
        }
        self.state = AudioCaptureState::Stopped;
        self.status()
    }

    /// Immediately tears down capture for app exit, cancellation, and terminal
    /// failure. Unlike [`Self::stop`], it never asks the live chunker to emit a
    /// final STT tail.
    pub(crate) async fn abort(&mut self) {
        self.graceful_stopping.store(false, Ordering::Release);
        self.stopping.store(true, Ordering::Release);
        let _ = self.bridge.stop();
        self.abort_processing_and_sender().await;
        self.finalized_queue.take();
        let mut pipeline = self.pipeline.lock().expect("processing pipeline lock");
        fail_recording_branch(&mut pipeline);
        pipeline.reset();
        self.state = AudioCaptureState::Stopped;
    }

    async fn abort_processing_and_sender(&mut self) {
        if let Some(task) = self.processing_task.take() {
            task.abort();
            let _ = task.await;
        }
        self.abort_sender_task().await;
    }

    async fn abort_sender_task(&mut self) {
        if let Some(sender_task) = self.sender_task.take() {
            sender_task.abort().await;
        }
    }

    pub(crate) fn status(&mut self) -> Result<AudioCaptureStatus, AudioCaptureCoordinatorError> {
        self.observe_sender_failure();
        let message = if self.state == AudioCaptureState::Failed {
            Some(SUBMISSION_FAILURE_MESSAGE.to_owned())
        } else if self
            .pipeline
            .lock()
            .expect("processing pipeline lock")
            .recording_unavailable
        {
            Some(RECORDING_UNAVAILABLE_MESSAGE.to_owned())
        } else if self
            .pipeline
            .lock()
            .expect("processing pipeline lock")
            .submission_gap
            || self
                .finalized_queue
                .as_ref()
                .is_some_and(|queue| queue.overloaded())
        {
            Some(GAP_MESSAGE.to_owned())
        } else {
            None
        };
        AudioCaptureStatus::new(self.state, message)
            .map_err(|_| AudioCaptureCoordinatorError::StatusFailed)
    }

    fn observe_sender_failure(&mut self) {
        if !self.processing_failed.load(Ordering::Acquire)
            && !self
                .sender_task
                .as_ref()
                .is_some_and(ChunkSenderTask::failed)
        {
            return;
        }
        let _ = self.bridge.stop();
        if let Some(task) = self.processing_task.take() {
            #[cfg(debug_assertions)]
            eprintln!("audio-capture processing worker aborted reason=sender_failure");
            task.abort();
        }
        if let Some(queue) = &self.finalized_queue {
            queue.clear();
        }
        let mut pipeline = self.pipeline.lock().expect("processing pipeline lock");
        fail_recording_branch(&mut pipeline);
        pipeline.reset();
        self.state = AudioCaptureState::Failed;
    }
}

fn remaining_graceful_stop_time(started_at: Instant) -> Duration {
    GRACEFUL_SENDER_DRAIN_TIMEOUT
        .checked_sub(started_at.elapsed())
        .unwrap_or(Duration::ZERO)
}

async fn run_processing_worker(
    mut receiver: mpsc::Receiver<NativeAudioFrame>,
    reorder_mixed_sources: bool,
    pipeline: Arc<Mutex<ProcessingPipeline>>,
    finalized_queue: Arc<FinalizedChunkQueue>,
    processing_failed: Arc<AtomicBool>,
    final_tail_admission_failed: Arc<AtomicBool>,
    stopping: Arc<AtomicBool>,
    graceful_stopping: Arc<AtomicBool>,
    sender_failed: Arc<AtomicBool>,
    sender_failure_notify: Arc<Notify>,
    worker_ready: oneshot::Sender<()>,
    #[cfg(debug_assertions)] generation: u64,
) {
    #[cfg(debug_assertions)]
    let mut exit_guard = ProcessingWorkerExitGuard::new(generation);
    #[cfg(debug_assertions)]
    eprintln!("audio-capture processing worker started generation={generation}");
    let _ = worker_ready.send(());
    #[cfg(debug_assertions)]
    let mut received_frame_count = 0_u64;
    #[cfg(debug_assertions)]
    let mut processed_frame_count = 0_u64;
    #[cfg(debug_assertions)]
    let mut throughput_metrics = ProcessingWorkerMetrics::new();
    let mut reorder = NativeFrameReorderBuffer::new(reorder_mixed_sources);

    loop {
        if sender_failed.load(Ordering::Acquire) {
            finalized_queue.clear();
            processing_failed.store(true, Ordering::Release);
            #[cfg(debug_assertions)]
            {
                report_reorder_metrics(generation, reorder.metrics());
                exit_guard.set_reason("sender_failed");
            }
            return;
        }
        let native_frame = tokio::select! {
            frame = receiver.recv() => frame,
            () = sender_failure_notify.notified() => {
                finalized_queue.clear();
                processing_failed.store(true, Ordering::Release);
                #[cfg(debug_assertions)]
                exit_guard.set_reason("sender_failed");
                return;
            }
        };
        let (released_frames, stopping_after_flush) = if let Some(native_frame) = native_frame {
            #[cfg(debug_assertions)]
            {
                received_frame_count += 1;
                if received_frame_count == 1 {
                    eprintln!("audio-capture processing worker received count=1");
                }
            }
            #[cfg(debug_assertions)]
            if received_frame_count == 1 {
                eprintln!(
                    "audio-capture processing frame format source={:?} sample_format={:?} sample_rate_hz={} channels={} interleaved={}",
                    native_frame.source(),
                    native_frame.format().sample_format(),
                    native_frame.format().sample_rate_hz(),
                    native_frame.format().channels(),
                    native_frame.format().interleaved(),
                );
            }
            (reorder.push(native_frame), false)
        } else {
            if !stopping.load(Ordering::Acquire) {
                processing_failed.store(true, Ordering::Release);
                #[cfg(debug_assertions)]
                {
                    report_reorder_metrics(generation, reorder.metrics());
                    exit_guard.set_reason("native_channel_closed");
                }
                return;
            } else if !graceful_stopping.load(Ordering::Acquire) {
                #[cfg(debug_assertions)]
                {
                    report_reorder_metrics(generation, reorder.metrics());
                    exit_guard.set_reason("aborted");
                }
                return;
            } else {
                #[cfg(debug_assertions)]
                eprintln!("audio-capture processing worker flushing reordered frames");
            }
            (reorder.flush(), true)
        };

        for OrderedNativeFrame {
            frame: native_frame,
            arrival_sequence,
        } in released_frames
        {
            #[cfg(debug_assertions)]
            let source = native_frame.source();
            #[cfg(debug_assertions)]
            let input_duration_microseconds = native_frame.duration_microseconds();
            #[cfg(debug_assertions)]
            let processing_started_at = Instant::now();
            #[cfg(debug_assertions)]
            {
                processed_frame_count += 1;
                set_timeline_processing_context(&pipeline, generation, arrival_sequence);
            }
            let result = process_native_frame(&pipeline, &finalized_queue, native_frame);
            #[cfg(debug_assertions)]
            {
                let processing_elapsed = processing_started_at.elapsed();
                if throughput_metrics.record_frame(
                    source,
                    input_duration_microseconds,
                    processing_elapsed,
                    result.is_ok(),
                ) {
                    eprintln!(
                        "audio-capture processing frame slow generation={generation} source={} elapsed_ms={} slow_frame_count={}",
                        source_identifier(source),
                        processing_elapsed.as_millis(),
                        throughput_metrics.slow_frame_count,
                    );
                }
                throughput_metrics.report_if_due(generation, processed_frame_count);
            }
            if let Err(stage) = result {
                #[cfg(debug_assertions)]
                {
                    if matches!(stage, ProcessingFailureStage::ChunkerTimelineRegression) {
                        report_first_timeline_regression(&pipeline);
                    }
                    report_reorder_metrics(generation, reorder.metrics());
                    eprintln!("audio-capture processing failed stage={stage}");
                }
                finalized_queue.clear();
                processing_failed.store(true, Ordering::Release);
                #[cfg(debug_assertions)]
                exit_guard.set_reason("processing_failed");
                return;
            }
        }
        if stopping_after_flush {
            if let Err(stage) = flush_processing_pipeline(&pipeline, &finalized_queue) {
                #[cfg(debug_assertions)]
                {
                    if matches!(stage, ProcessingFailureStage::ChunkerTimelineRegression) {
                        report_first_timeline_regression(&pipeline);
                    }
                    report_reorder_metrics(generation, reorder.metrics());
                    eprintln!("audio-capture processing failed stage={stage}");
                }
                finalized_queue.clear();
                processing_failed.store(true, Ordering::Release);
                #[cfg(debug_assertions)]
                exit_guard.set_reason("processing_failed");
                return;
            }
            if let Err(stage) = flush_final_live_chunk(&pipeline, &finalized_queue) {
                #[cfg(debug_assertions)]
                {
                    report_reorder_metrics(generation, reorder.metrics());
                    eprintln!("audio-capture processing failed stage={stage}");
                }
                if matches!(stage, ProcessingFailureStage::FinalTailQueueFull) {
                    final_tail_admission_failed.store(true, Ordering::Release);
                } else {
                    finalized_queue.clear();
                }
                processing_failed.store(true, Ordering::Release);
                #[cfg(debug_assertions)]
                exit_guard.set_reason("processing_failed");
                return;
            }
            #[cfg(debug_assertions)]
            {
                report_reorder_metrics(generation, reorder.metrics());
                exit_guard.set_reason("stopped");
            }
            return;
        }
    }
}

#[cfg(debug_assertions)]
fn report_reorder_metrics(generation: u64, metrics: super::reorder::ReorderMetrics) {
    if metrics.high_water_mark == 0 {
        return;
    }
    eprintln!(
        "audio-capture pts reorder summary generation={generation} reordered_frame_count={} max_observed_skew_us={} high_water_mark={} forced_release_count={} shutdown_flush_count={}",
        metrics.reordered_frame_count,
        metrics.max_observed_skew_microseconds,
        metrics.high_water_mark,
        metrics.forced_release_count,
        metrics.shutdown_flush_count,
    );
}

#[cfg(debug_assertions)]
const fn source_identifier(source: NativeAudioSource) -> &'static str {
    match source {
        NativeAudioSource::SystemAudio => "system_audio",
        NativeAudioSource::Microphone => "microphone",
    }
}

#[cfg(debug_assertions)]
struct ProcessingWorkerExitGuard {
    generation: u64,
    reason: &'static str,
}

#[cfg(debug_assertions)]
impl ProcessingWorkerExitGuard {
    fn new(generation: u64) -> Self {
        Self {
            generation,
            reason: "cancelled_or_panicked",
        }
    }

    fn set_reason(&mut self, reason: &'static str) {
        self.reason = reason;
    }
}

#[cfg(debug_assertions)]
impl Drop for ProcessingWorkerExitGuard {
    fn drop(&mut self) {
        eprintln!(
            "audio-capture processing worker exited reason={} generation={}",
            self.reason, self.generation
        );
    }
}

fn process_native_frame(
    pipeline: &Arc<Mutex<ProcessingPipeline>>,
    finalized_queue: &Arc<FinalizedChunkQueue>,
    native_frame: NativeAudioFrame,
) -> Result<(), ProcessingFailureStage> {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    #[cfg(debug_assertions)]
    let source = native_frame.source();
    #[cfg(debug_assertions)]
    let incoming_timestamp = native_frame.capture_time_seconds();
    #[cfg(debug_assertions)]
    let frame_duration_microseconds = native_frame.duration_microseconds();
    #[cfg(debug_assertions)]
    let sample_rate_hz = native_frame.format().sample_rate_hz();
    #[cfg(debug_assertions)]
    let channels = native_frame.format().channels();
    #[cfg(debug_assertions)]
    let sample_format = native_frame.format().sample_format();
    #[cfg(debug_assertions)]
    let interleaved = native_frame.format().interleaved();
    let frames = pipeline
        .processor
        .process(native_frame)
        .map_err(ProcessingFailureStage::from)?;
    process_mixed_frames(
        &mut pipeline,
        finalized_queue,
        frames,
        #[cfg(debug_assertions)]
        source,
        #[cfg(debug_assertions)]
        incoming_timestamp,
        #[cfg(debug_assertions)]
        frame_duration_microseconds,
        #[cfg(debug_assertions)]
        sample_rate_hz,
        #[cfg(debug_assertions)]
        channels,
        #[cfg(debug_assertions)]
        sample_format,
        #[cfg(debug_assertions)]
        interleaved,
    )
}

fn flush_processing_pipeline(
    pipeline: &Arc<Mutex<ProcessingPipeline>>,
    finalized_queue: &Arc<FinalizedChunkQueue>,
) -> Result<(), ProcessingFailureStage> {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    let frames = pipeline
        .processor
        .flush()
        .map_err(ProcessingFailureStage::from)?;
    process_mixed_frames(
        &mut pipeline,
        finalized_queue,
        frames,
        #[cfg(debug_assertions)]
        NativeAudioSource::SystemAudio,
        #[cfg(debug_assertions)]
        0.0,
        #[cfg(debug_assertions)]
        0,
        #[cfg(debug_assertions)]
        0,
        #[cfg(debug_assertions)]
        0,
        #[cfg(debug_assertions)]
        super::types::NativeSampleFormat::Float32,
        #[cfg(debug_assertions)]
        true,
    )
}

/// Finalizes the live rolling stream after all native/reordered/mixed frames
/// have entered it. Recording finalization intentionally remains separate.
fn flush_final_live_chunk(
    pipeline: &Arc<Mutex<ProcessingPipeline>>,
    finalized_queue: &Arc<FinalizedChunkQueue>,
) -> Result<(), ProcessingFailureStage> {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    let Some(chunk) = pipeline.chunker.flush_final() else {
        return Ok(());
    };
    let encoded = encode_audio_chunk(chunk).map_err(|_| ProcessingFailureStage::WavEncode)?;
    #[cfg(debug_assertions)]
    eprintln!(
        "audio-capture final live chunk finalized sequence={}",
        encoded.sequence
    );
    match finalized_queue.push(encoded) {
        Ok(()) => Ok(()),
        Err(FinalizedChunkQueueError::Full) => {
            pipeline.submission_gap = true;
            Err(ProcessingFailureStage::FinalTailQueueFull)
        }
        Err(FinalizedChunkQueueError::Closed) => Err(ProcessingFailureStage::SenderQueueClosed),
    }
}

#[allow(clippy::too_many_arguments)]
fn process_mixed_frames(
    pipeline: &mut ProcessingPipeline,
    finalized_queue: &Arc<FinalizedChunkQueue>,
    frames: Vec<MixedAudioFrame>,
    #[cfg(debug_assertions)] source: NativeAudioSource,
    #[cfg(debug_assertions)] incoming_timestamp: f64,
    #[cfg(debug_assertions)] frame_duration_microseconds: u64,
    #[cfg(debug_assertions)] sample_rate_hz: u32,
    #[cfg(debug_assertions)] channels: u16,
    #[cfg(debug_assertions)] sample_format: super::types::NativeSampleFormat,
    #[cfg(debug_assertions)] interleaved: bool,
) -> Result<(), ProcessingFailureStage> {
    for frame in frames {
        process_recording_frame(pipeline, &frame);
        #[cfg(debug_assertions)]
        let previous_expected_timestamp = pipeline.chunker.expected_next_time_for_diagnostics();
        #[cfg(debug_assertions)]
        let previous_source = pipeline.timeline_diagnostics.previous_source;
        #[cfg(debug_assertions)]
        let previous_accepted_timestamp = pipeline.timeline_diagnostics.previous_accepted_timestamp;
        #[cfg(debug_assertions)]
        let chunker_timestamp = frame.capture_time_seconds;
        let result = match pipeline.chunker.push(frame) {
            Ok(result) => result,
            Err(AudioChunkerError::TimelineRegression) => {
                #[cfg(debug_assertions)]
                {
                    pipeline.timeline_diagnostics.last_regression =
                        Some(TimelineRegressionDiagnostic {
                            source,
                            incoming_timestamp,
                            chunker_timestamp,
                            previous_source,
                            previous_accepted_timestamp,
                            previous_expected_timestamp,
                            frame_duration_microseconds,
                            sample_rate_hz,
                            channels,
                            sample_format,
                            interleaved,
                            processing_arrival_sequence: pipeline
                                .timeline_diagnostics
                                .processing_arrival_sequence,
                            generation: pipeline.timeline_diagnostics.generation,
                        });
                }
                return Err(ProcessingFailureStage::ChunkerTimelineRegression);
            }
            Err(error) => return Err(ProcessingFailureStage::from(error)),
        };
        #[cfg(debug_assertions)]
        {
            pipeline.timeline_diagnostics.previous_source = Some(source);
            pipeline.timeline_diagnostics.previous_accepted_timestamp = Some(chunker_timestamp);
        }
        if result.gap_detected {
            pipeline.submission_gap = true;
        }
        for chunk in result.chunks {
            let encoded =
                encode_audio_chunk(chunk).map_err(|_| ProcessingFailureStage::WavEncode)?;
            #[cfg(debug_assertions)]
            let (finalized_count, cadence_ms, elapsed_ms) =
                pipeline.finalized_chunk_metrics.note_finalized();
            #[cfg(debug_assertions)]
            eprintln!(
                "audio-capture chunk finalized sequence={} finalized_count={finalized_count} cadence_ms={cadence_ms} elapsed_ms={elapsed_ms}",
                encoded.sequence,
            );
            match finalized_queue.push(encoded) {
                Ok(()) => {}
                // Keep the older FIFO entries intact and continue capture.
                // The public status makes this bounded overload explicit;
                // the client owns protocol sequence assignment for later
                // successfully submitted chunks.
                Err(FinalizedChunkQueueError::Full) => {
                    pipeline.submission_gap = true;
                }
                Err(FinalizedChunkQueueError::Closed) => {
                    return Err(ProcessingFailureStage::SenderQueueClosed);
                }
            }
        }
    }
    Ok(())
}

#[cfg(debug_assertions)]
fn set_timeline_processing_context(
    pipeline: &Arc<Mutex<ProcessingPipeline>>,
    generation: u64,
    processing_arrival_sequence: u64,
) {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    pipeline.timeline_diagnostics.generation = generation;
    pipeline.timeline_diagnostics.processing_arrival_sequence = processing_arrival_sequence;
}

#[cfg(debug_assertions)]
fn report_first_timeline_regression(pipeline: &Arc<Mutex<ProcessingPipeline>>) {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    let diagnostics = &mut pipeline.timeline_diagnostics;
    if diagnostics.regression_reported {
        return;
    }
    let Some(snapshot) = diagnostics.last_regression else {
        return;
    };
    diagnostics.regression_reported = true;
    let previous_source = snapshot
        .previous_source
        .map(source_identifier)
        .unwrap_or("none");
    let previous_accepted_timestamp = snapshot.previous_accepted_timestamp.unwrap_or(-1.0);
    let previous_expected_timestamp = snapshot.previous_expected_timestamp.unwrap_or(-1.0);
    eprintln!(
        "audio-capture chunker timeline regression generation={} arrival_sequence={} source={} incoming_timestamp={:.6} chunker_timestamp={:.6} previous_source={} previous_accepted_timestamp={:.6} previous_expected_timestamp={:.6} timestamp_delta={:.6} frame_duration_us={} sample_rate_hz={} channels={} sample_format={:?} interleaved={}",
        snapshot.generation,
        snapshot.processing_arrival_sequence,
        source_identifier(snapshot.source),
        snapshot.incoming_timestamp,
        snapshot.chunker_timestamp,
        previous_source,
        previous_accepted_timestamp,
        previous_expected_timestamp,
        snapshot.chunker_timestamp - previous_expected_timestamp,
        snapshot.frame_duration_microseconds,
        snapshot.sample_rate_hz,
        snapshot.channels,
        snapshot.sample_format,
        snapshot.interleaved,
    );
}

#[derive(Debug, Clone, Copy)]
enum ProcessingFailureStage {
    ProcessorInvalidFrame,
    ProcessorUnsupportedLayout,
    ProcessorInvalidTimeline,
    ChunkerInvalidFrame,
    ChunkerTimelineRegression,
    WavEncode,
    SenderQueueClosed,
    FinalTailQueueFull,
}

impl From<AudioChunkerError> for ProcessingFailureStage {
    fn from(error: AudioChunkerError) -> Self {
        match error {
            AudioChunkerError::InvalidFrame => Self::ChunkerInvalidFrame,
            AudioChunkerError::TimelineRegression => Self::ChunkerTimelineRegression,
        }
    }
}

impl From<AudioProcessingError> for ProcessingFailureStage {
    fn from(error: AudioProcessingError) -> Self {
        match error {
            AudioProcessingError::InvalidFrame => Self::ProcessorInvalidFrame,
            AudioProcessingError::UnsupportedLayout => Self::ProcessorUnsupportedLayout,
            AudioProcessingError::InvalidTimeline => Self::ProcessorInvalidTimeline,
        }
    }
}

impl std::fmt::Display for ProcessingFailureStage {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let stage = match self {
            Self::ProcessorInvalidFrame => "processor_invalid_frame",
            Self::ProcessorUnsupportedLayout => "processor_unsupported_layout",
            Self::ProcessorInvalidTimeline => "processor_invalid_timeline",
            Self::ChunkerInvalidFrame => "chunker_invalid_frame",
            Self::ChunkerTimelineRegression => "chunker_timeline_regression",
            Self::WavEncode => "wav_encode",
            Self::SenderQueueClosed => "sender_queue_closed",
            Self::FinalTailQueueFull => "final_tail_queue_full",
        };
        formatter.write_str(stage)
    }
}

fn process_recording_frame(
    pipeline: &mut ProcessingPipeline,
    frame: &super::mixer::MixedAudioFrame,
) {
    let Some(branch) = pipeline.recording.as_mut() else {
        return;
    };
    let result = branch
        .chunker
        .push(frame.capture_time_seconds, &frame.samples);
    let Ok(result) = result else {
        fail_recording_branch(pipeline);
        return;
    };
    if result.gap_detected {
        branch.writer.set_has_gaps(true);
    }
    for chunk in result.chunks {
        let wav_bytes = match build_wav_from_samples(&chunk.samples) {
            Ok(wav_bytes) => wav_bytes,
            Err(_) => {
                fail_recording_branch(pipeline);
                return;
            }
        };
        let segment = match EncodedRecordingSegment::new(
            chunk.segment_index,
            wav_bytes,
            chunk.sample_count,
        ) {
            Ok(segment) => segment,
            Err(_) => {
                fail_recording_branch(pipeline);
                return;
            }
        };
        if branch.writer.try_enqueue(segment).is_err() {
            fail_recording_branch(pipeline);
            return;
        }
    }
}

fn fail_recording_branch(pipeline: &mut ProcessingPipeline) {
    let Some(branch) = pipeline.recording.take() else {
        return;
    };
    pipeline.recording_unavailable = true;
    tokio::spawn(async move {
        branch
            .writer
            .abort(RecordingFailureCode::WriterFailed)
            .await;
    });
}

fn finalize_recording_branch(
    pipeline: &Arc<Mutex<ProcessingPipeline>>,
) -> Option<(Arc<dyn RecordingSegmentWriter>, bool)> {
    let mut pipeline = pipeline.lock().expect("processing pipeline lock");
    let mut branch = pipeline.recording.take()?;
    let mut failed = false;
    if let Some(chunk) = branch.chunker.flush_final() {
        let result = build_wav_from_samples(&chunk.samples).and_then(|wav_bytes| {
            EncodedRecordingSegment::new(chunk.segment_index, wav_bytes, chunk.sample_count)
                .map_err(|_| super::pcm16::AudioEncodingError::InvalidChunk)
        });
        match result {
            Ok(segment) => {
                if branch.writer.try_enqueue(segment).is_err() {
                    pipeline.recording_unavailable = true;
                    failed = true;
                }
            }
            Err(_) => {
                pipeline.recording_unavailable = true;
                failed = true;
            }
        }
    }
    Some((branch.writer, failed))
}

async fn finish_recording_finalization(
    finalization: Option<(Arc<dyn RecordingSegmentWriter>, bool)>,
) {
    if let Some((writer, failed)) = finalization {
        if failed {
            writer.abort(RecordingFailureCode::FinalizationFailed).await;
        } else {
            let _ = writer.finalize().await;
        }
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum AudioCaptureCoordinatorError {
    #[error("Audio capture is already starting or active.")]
    AlreadyStarted,
    #[error("Audio capture could not be started.")]
    StartFailed,
    #[error("Audio capture could not be stopped.")]
    StopFailed,
    #[error("Audio capture could not finish pending transcription.")]
    GracefulFinishFailed,
    #[error("Audio capture is not active.")]
    NotCapturing,
    #[error("Audio processing failed.")]
    ProcessingFailed,
    #[error("Audio encoding failed.")]
    EncodingFailed,
    #[error("Audio submission is unavailable.")]
    SubmissionUnavailable,
    #[error("Audio capture state is invalid.")]
    StatusFailed,
}

#[cfg(test)]
mod tests {
    use std::{
        future::Future,
        pin::Pin,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc, Mutex,
        },
        time::Duration,
    };

    use tokio::{
        sync::{mpsc, oneshot, Notify},
        time::{sleep, timeout},
    };

    use super::{
        process_native_frame, run_processing_worker, AudioCaptureCoordinator,
        AudioCaptureCoordinatorError, ProcessingPipeline, GAP_MESSAGE,
    };
    use crate::{
        audio_capture::{
            bridge::{
                NativeAudioCaptureBridge, NativeAudioCaptureBridgeError, NativeAudioFrameSender,
            },
            chunker::CHUNK_SAMPLES,
            mixer::MixedAudioFrame,
            recording_writer::{
                EncodedRecordingSegment, RecordingFailureCode, RecordingFuture,
                RecordingSegmentWriter, RecordingWriterError,
            },
            sender::{
                ChunkSubmitter, FinalizedChunkQueue, FinalizedChunkQueueError,
                FINALIZED_CHUNK_QUEUE_CAPACITY,
            },
            status::{AudioCaptureState, AudioCaptureStatus},
            types::{
                AudioCaptureConfiguration, NativeAudioFormat, NativeAudioFrame, NativeAudioSamples,
                NativeAudioSource, NativeSampleFormat,
            },
            wav::EncodedAudioChunk,
        },
        live_transcription::client::{ChunkSubmissionResult, LiveTranscriptionClientError},
    };

    struct FakeBridge {
        sender: Arc<Mutex<Option<NativeAudioFrameSender>>>,
        stop_calls: Arc<Mutex<usize>>,
    }

    impl NativeAudioCaptureBridge for FakeBridge {
        fn start(
            &mut self,
            _configuration: &AudioCaptureConfiguration,
            frame_sender: NativeAudioFrameSender,
        ) -> Result<(), NativeAudioCaptureBridgeError> {
            *self.sender.lock().expect("fake bridge lock") = Some(frame_sender);
            Ok(())
        }

        fn stop(&mut self) -> Result<(), NativeAudioCaptureBridgeError> {
            *self.stop_calls.lock().expect("fake bridge lock") += 1;
            self.sender.lock().expect("fake bridge lock").take();
            Ok(())
        }

        fn status(&self) -> AudioCaptureStatus {
            AudioCaptureStatus::new(AudioCaptureState::Stopped, None).expect("valid status")
        }
    }

    struct FakeSubmitter;

    impl ChunkSubmitter for FakeSubmitter {
        fn submit(
            &self,
            _chunk: EncodedAudioChunk,
            _upstream_pending_chunks: u8,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            Box::pin(async {
                Ok(ChunkSubmissionResult {
                    sequence: 0,
                    skipped_silence: false,
                    accepted_segment_count: 0,
                    gap_reported: false,
                })
            })
        }
    }

    struct RecordingSubmitter {
        sequences: Arc<Mutex<Vec<u64>>>,
    }

    struct FailingSubmitter;

    struct FakeRecordingWriter {
        segments: Arc<Mutex<Vec<(u32, usize, Vec<u8>)>>>,
        finalized: Arc<Mutex<usize>>,
        aborted: Arc<Mutex<usize>>,
        fail_enqueue: bool,
    }

    impl RecordingSegmentWriter for FakeRecordingWriter {
        fn try_enqueue(
            &self,
            segment: EncodedRecordingSegment,
        ) -> Result<(), RecordingWriterError> {
            if self.fail_enqueue {
                return Err(RecordingWriterError::Unavailable);
            }
            self.segments
                .lock()
                .expect("recording segments lock")
                .push((
                    segment.segment_index,
                    segment.sample_count,
                    segment.wav_bytes,
                ));
            Ok(())
        }

        fn set_has_gaps(&self, _has_gaps: bool) {}

        fn finalize<'a>(&'a self) -> RecordingFuture<'a, ()> {
            let finalized = Arc::clone(&self.finalized);
            Box::pin(async move {
                *finalized.lock().expect("finalized lock") += 1;
                Ok(())
            })
        }

        fn abort<'a>(
            &'a self,
            _failure_code: RecordingFailureCode,
        ) -> Pin<Box<dyn Future<Output = ()> + Send + 'a>> {
            let aborted = Arc::clone(&self.aborted);
            Box::pin(async move {
                *aborted.lock().expect("aborted lock") += 1;
            })
        }
    }

    impl ChunkSubmitter for FailingSubmitter {
        fn submit(
            &self,
            _chunk: EncodedAudioChunk,
            _upstream_pending_chunks: u8,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            Box::pin(async { Err(LiveTranscriptionClientError::ConnectionFailed) })
        }
    }

    impl ChunkSubmitter for RecordingSubmitter {
        fn submit(
            &self,
            chunk: EncodedAudioChunk,
            _upstream_pending_chunks: u8,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            Box::pin(async move {
                self.sequences
                    .lock()
                    .expect("recording submitter lock")
                    .push(chunk.sequence);
                Ok(ChunkSubmissionResult {
                    sequence: chunk.sequence,
                    skipped_silence: false,
                    accepted_segment_count: 0,
                    gap_reported: false,
                })
            })
        }
    }

    fn bridge() -> (FakeBridge, Arc<Mutex<Option<NativeAudioFrameSender>>>) {
        let sender = Arc::new(Mutex::new(None));
        (
            FakeBridge {
                sender: Arc::clone(&sender),
                stop_calls: Arc::new(Mutex::new(0)),
            },
            sender,
        )
    }

    fn native_frame(sample_count: usize) -> NativeAudioFrame {
        native_frame_at(sample_count, 0.0)
    }

    fn system_only_configuration() -> AudioCaptureConfiguration {
        AudioCaptureConfiguration::new(0, None, true, false, true)
            .expect("system-only configuration")
    }

    fn native_frame_at(sample_count: usize, capture_time_seconds: f64) -> NativeAudioFrame {
        NativeAudioFrame::new(
            NativeAudioSource::SystemAudio,
            NativeAudioFormat::new(16_000, 1, NativeSampleFormat::Float32, true).expect("format"),
            capture_time_seconds,
            NativeAudioSamples::Float32(vec![0.0; sample_count]),
        )
        .expect("frame")
    }

    #[cfg(debug_assertions)]
    #[test]
    fn processing_metrics_record_source_duration_and_rate_limited_slow_frames() {
        let mut metrics = super::ProcessingWorkerMetrics::new();

        assert!(!metrics.record_frame(
            NativeAudioSource::Microphone,
            10_000,
            Duration::from_millis(1),
            true,
        ));
        assert!(metrics.record_frame(
            NativeAudioSource::SystemAudio,
            10_000,
            Duration::from_millis(60),
            true,
        ));

        assert_eq!(metrics.completed_frame_count, 2);
        assert_eq!(metrics.failed_frame_count, 0);
        assert_eq!(metrics.microphone.input_microseconds, 10_000);
        assert_eq!(metrics.system.input_microseconds, 10_000);
        assert_eq!(metrics.slow_frame_count, 1);
    }

    #[tokio::test]
    async fn processing_worker_signals_readiness_before_capture_admission() {
        let (sender, receiver) = mpsc::channel(1);
        let (ready_sender, ready_receiver) = oneshot::channel();
        let processing_failed = Arc::new(AtomicBool::new(false));
        let stopping = Arc::new(AtomicBool::new(false));
        let sender_failed = Arc::new(AtomicBool::new(false));
        let worker = tokio::spawn(run_processing_worker(
            receiver,
            false,
            Arc::new(Mutex::new(super::ProcessingPipeline::default())),
            FinalizedChunkQueue::new(),
            Arc::clone(&processing_failed),
            Arc::new(AtomicBool::new(false)),
            stopping,
            Arc::new(AtomicBool::new(false)),
            sender_failed,
            Arc::new(Notify::new()),
            ready_sender,
            1,
        ));

        timeout(Duration::from_secs(1), ready_receiver)
            .await
            .expect("worker signals readiness")
            .expect("worker has not exited");
        assert!(!processing_failed.load(Ordering::Acquire));

        drop(sender);
        timeout(Duration::from_secs(1), worker)
            .await
            .expect("worker exits after its input closes")
            .expect("worker does not panic");
    }

    #[test]
    fn processes_valid_screen_capturekit_style_microphone_and_system_frames() {
        let microphone_frame = NativeAudioFrame::new(
            NativeAudioSource::Microphone,
            NativeAudioFormat::new(48_000, 1, NativeSampleFormat::Float32, false).expect("format"),
            1.0,
            NativeAudioSamples::Float32(vec![0.0; 1_024]),
        )
        .expect("native microphone frame");
        let frame = NativeAudioFrame::new(
            NativeAudioSource::SystemAudio,
            NativeAudioFormat::new(48_000, 2, NativeSampleFormat::Float32, false).expect("format"),
            1.0 + 1_024.0 / 16_000.0,
            NativeAudioSamples::Float32(vec![0.0; 2_048]),
        )
        .expect("native frame");
        let pipeline = Arc::new(Mutex::new(ProcessingPipeline::default()));

        process_native_frame(&pipeline, &FinalizedChunkQueue::new(), microphone_frame)
            .expect("valid microphone frame processes without a chunk");
        process_native_frame(&pipeline, &FinalizedChunkQueue::new(), frame)
            .expect("valid native frame processes without a chunk");
    }

    #[test]
    fn accepts_successive_48khz_microphone_buffer_presentation_timestamps() {
        let pipeline = Arc::new(Mutex::new(ProcessingPipeline::default()));
        let start = 1.0;
        let native_buffer_duration = 512.0 / 48_000.0;
        for timestamp in [start, start + native_buffer_duration] {
            let frame = NativeAudioFrame::new(
                NativeAudioSource::Microphone,
                NativeAudioFormat::new(48_000, 1, NativeSampleFormat::Float32, false)
                    .expect("format"),
                timestamp,
                NativeAudioSamples::Float32(vec![0.0; 512]),
            )
            .expect("native frame");
            process_native_frame(&pipeline, &FinalizedChunkQueue::new(), frame)
                .expect("presentation-timestamped frame is contiguous");
        }
    }

    #[cfg(debug_assertions)]
    #[test]
    fn records_one_structural_snapshot_for_a_mixed_source_timeline_regression() {
        let pipeline = Arc::new(Mutex::new(ProcessingPipeline::default()));
        let queue = FinalizedChunkQueue::new();
        let system_frame = NativeAudioFrame::new(
            NativeAudioSource::SystemAudio,
            NativeAudioFormat::new(16_000, 1, NativeSampleFormat::Float32, true).expect("format"),
            1.0,
            NativeAudioSamples::Float32(vec![0.0; 160]),
        )
        .expect("system frame");
        let microphone_frame = NativeAudioFrame::new(
            NativeAudioSource::Microphone,
            NativeAudioFormat::new(16_000, 1, NativeSampleFormat::Float32, true).expect("format"),
            1.0,
            NativeAudioSamples::Float32(vec![0.0; 160]),
        )
        .expect("microphone frame");

        super::set_timeline_processing_context(&pipeline, 7, 1);
        process_native_frame(&pipeline, &queue, system_frame).expect("first source processes");
        super::set_timeline_processing_context(&pipeline, 7, 2);
        assert!(matches!(
            process_native_frame(&pipeline, &queue, microphone_frame),
            Err(super::ProcessingFailureStage::ChunkerTimelineRegression)
        ));

        let pipeline = pipeline.lock().expect("pipeline lock");
        let snapshot = pipeline
            .timeline_diagnostics
            .last_regression
            .expect("regression snapshot");
        assert_eq!(snapshot.generation, 7);
        assert_eq!(snapshot.processing_arrival_sequence, 2);
        assert_eq!(snapshot.source, NativeAudioSource::Microphone);
        assert_eq!(
            snapshot.previous_source,
            Some(NativeAudioSource::SystemAudio)
        );
        assert_eq!(snapshot.incoming_timestamp, 1.0);
        assert_eq!(snapshot.chunker_timestamp, 1.0);
        assert_eq!(snapshot.previous_accepted_timestamp, Some(1.0));
        assert!(snapshot
            .previous_expected_timestamp
            .is_some_and(|timestamp| timestamp > snapshot.chunker_timestamp));
        assert_eq!(snapshot.frame_duration_microseconds, 10_000);
    }

    #[test]
    fn sender_queue_overload_marks_a_gap_without_failing_the_processing_pipeline() {
        let queue = FinalizedChunkQueue::new();
        for sequence in 0..FINALIZED_CHUNK_QUEUE_CAPACITY as u64 {
            queue
                .push(EncodedAudioChunk {
                    sequence,
                    capture_started_at_seconds: sequence as f64,
                    overlap_seconds: 1.0,
                    wav_payload: vec![0],
                })
                .expect("fills bounded queue");
        }

        let pipeline = Arc::new(Mutex::new(ProcessingPipeline::default()));
        assert!(process_native_frame(&pipeline, &queue, native_frame(CHUNK_SAMPLES + 1)).is_ok());
        assert!(queue.overloaded());
        assert!(
            pipeline
                .lock()
                .expect("processing pipeline lock")
                .submission_gap
        );
        assert_eq!(
            queue.push(EncodedAudioChunk {
                sequence: FINALIZED_CHUNK_QUEUE_CAPACITY as u64 + 1,
                capture_started_at_seconds: 0.0,
                overlap_seconds: 1.0,
                wav_payload: vec![0],
            }),
            Err(FinalizedChunkQueueError::Full)
        );
    }

    #[tokio::test]
    async fn processing_worker_survives_sender_queue_overload() {
        let queue = FinalizedChunkQueue::new();
        for sequence in 0..FINALIZED_CHUNK_QUEUE_CAPACITY as u64 {
            queue
                .push(EncodedAudioChunk {
                    sequence,
                    capture_started_at_seconds: sequence as f64,
                    overlap_seconds: 1.0,
                    wav_payload: vec![0],
                })
                .expect("fills bounded queue");
        }
        let (native_sender, receiver) = mpsc::channel(1);
        let (ready_sender, ready_receiver) = oneshot::channel();
        let processing_failed = Arc::new(AtomicBool::new(false));
        let stopping = Arc::new(AtomicBool::new(false));
        let worker = tokio::spawn(run_processing_worker(
            receiver,
            false,
            Arc::new(Mutex::new(ProcessingPipeline::default())),
            Arc::clone(&queue),
            Arc::clone(&processing_failed),
            Arc::new(AtomicBool::new(false)),
            Arc::clone(&stopping),
            Arc::new(AtomicBool::new(false)),
            Arc::new(AtomicBool::new(false)),
            Arc::new(Notify::new()),
            ready_sender,
            1,
        ));
        ready_receiver
            .await
            .expect("worker signals readiness before processing frames");

        native_sender
            .send(native_frame(CHUNK_SAMPLES + 1))
            .await
            .expect("worker receiver remains available");
        timeout(Duration::from_secs(1), async {
            while !queue.overloaded() {
                sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("worker observes bounded queue overload");

        assert!(!processing_failed.load(Ordering::Acquire));
        assert!(!worker.is_finished());

        stopping.store(true, Ordering::Release);
        drop(native_sender);
        timeout(Duration::from_secs(1), worker)
            .await
            .expect("worker exits only after its input closes")
            .expect("worker does not panic");
    }

    #[test]
    fn sender_queue_overload_is_visible_through_the_generic_gap_status() {
        let (bridge, _) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let queue = FinalizedChunkQueue::new();
        for sequence in 0..FINALIZED_CHUNK_QUEUE_CAPACITY as u64 {
            assert_eq!(
                queue.push(EncodedAudioChunk {
                    sequence,
                    capture_started_at_seconds: sequence as f64,
                    overlap_seconds: 1.0,
                    wav_payload: vec![0],
                }),
                Ok(())
            );
        }
        assert_eq!(
            queue.push(EncodedAudioChunk {
                sequence: FINALIZED_CHUNK_QUEUE_CAPACITY as u64,
                capture_started_at_seconds: FINALIZED_CHUNK_QUEUE_CAPACITY as f64,
                overlap_seconds: 1.0,
                wav_payload: vec![0],
            }),
            Err(FinalizedChunkQueueError::Full)
        );
        coordinator.finalized_queue = Some(queue);
        coordinator.state = AudioCaptureState::Capturing;

        assert_eq!(
            coordinator.status().expect("status").message(),
            Some(GAP_MESSAGE)
        );
        assert_eq!(
            coordinator.status().expect("status").state(),
            AudioCaptureState::Capturing
        );
    }

    fn recording_writer(
        fail_enqueue: bool,
    ) -> (
        Arc<FakeRecordingWriter>,
        Arc<Mutex<Vec<(u32, usize, Vec<u8>)>>>,
    ) {
        let segments = Arc::new(Mutex::new(Vec::new()));
        (
            Arc::new(FakeRecordingWriter {
                segments: Arc::clone(&segments),
                finalized: Arc::new(Mutex::new(0)),
                aborted: Arc::new(Mutex::new(0)),
                fail_enqueue,
            }),
            segments,
        )
    }

    #[tokio::test]
    async fn coordinator_rejects_duplicate_start_and_stops_idempotently() {
        let (bridge, _) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let configuration = AudioCaptureConfiguration::default();
        assert_eq!(
            coordinator
                .start(&configuration, Arc::new(FakeSubmitter))
                .await
                .expect("starts")
                .state(),
            AudioCaptureState::Capturing
        );
        assert_eq!(
            coordinator
                .start(&configuration, Arc::new(FakeSubmitter))
                .await,
            Err(AudioCaptureCoordinatorError::AlreadyStarted)
        );
        assert_eq!(
            coordinator.stop().await.expect("stops").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(
            coordinator.stop().await.expect("stops again").state(),
            AudioCaptureState::Stopped
        );
    }

    #[tokio::test]
    async fn worker_drains_native_frames_and_submits_finalized_wav_chunks() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        coordinator
            .start(
                &system_only_configuration(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            // The stateful test resampler retains one boundary input sample,
            // so this produces exactly one canonical 64k-sample live window.
            .try_send(native_frame(CHUNK_SAMPLES + 1))
            .expect("callback stays non-blocking");

        timeout(Duration::from_secs(1), async {
            loop {
                if submitted
                    .lock()
                    .expect("recording submitter lock")
                    .as_slice()
                    == [0]
                {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("worker submits one finalized chunk");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame_at(2, (CHUNK_SAMPLES + 1) as f64 / 16_000.0))
            .expect("callback stays non-blocking");
        assert_eq!(
            coordinator.status().expect("status").state(),
            AudioCaptureState::Capturing
        );
        coordinator.stop().await.expect("stops");
        assert_eq!(
            *submitted.lock().expect("submitter lock"),
            vec![0, 1],
            "normal stop admits the retained overlap plus the newly uncovered tail"
        );
    }

    #[tokio::test]
    async fn graceful_stop_submits_a_short_live_tail_once_before_session_end() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        coordinator
            .start(
                &system_only_configuration(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(16_000))
            .expect("callback remains non-blocking");

        coordinator.stop().await.expect("graceful stop");
        assert_eq!(*submitted.lock().expect("submitter lock"), vec![0]);
        assert_eq!(
            coordinator.stop().await.expect("idempotent stop").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(
            *submitted.lock().expect("submitter lock"),
            vec![0],
            "repeated stop cannot duplicate the final tail"
        );
    }

    #[tokio::test]
    async fn graceful_stop_does_not_add_a_tail_at_an_exact_full_boundary() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        coordinator
            .start(
                &system_only_configuration(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(CHUNK_SAMPLES + 1))
            .expect("callback remains non-blocking");

        coordinator.stop().await.expect("graceful stop");
        assert_eq!(*submitted.lock().expect("submitter lock"), vec![0]);
    }

    #[tokio::test]
    async fn abort_never_emits_a_partial_live_tail() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        coordinator
            .start(
                &system_only_configuration(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(16_000))
            .expect("callback remains non-blocking");

        coordinator.abort().await;
        assert!(submitted.lock().expect("submitter lock").is_empty());
    }

    #[test]
    fn final_tail_queue_overflow_is_an_explicit_graceful_finish_failure() {
        let queue = FinalizedChunkQueue::new();
        for sequence in 0..FINALIZED_CHUNK_QUEUE_CAPACITY as u64 {
            queue
                .push(EncodedAudioChunk {
                    sequence,
                    capture_started_at_seconds: sequence as f64,
                    overlap_seconds: 1.0,
                    wav_payload: vec![0],
                })
                .expect("fills bounded queue");
        }
        let pipeline = Arc::new(Mutex::new(ProcessingPipeline::default()));
        {
            let mut pipeline = pipeline.lock().expect("pipeline lock");
            let _ = pipeline
                .chunker
                .push(MixedAudioFrame {
                    capture_time_seconds: 0.0,
                    samples: vec![0.0; CHUNK_SAMPLES + 1],
                })
                .expect("valid tail source");
        }

        assert!(matches!(
            super::flush_final_live_chunk(&pipeline, &queue),
            Err(super::ProcessingFailureStage::FinalTailQueueFull)
        ));
        assert_eq!(queue.len(), FINALIZED_CHUNK_QUEUE_CAPACITY);
    }

    #[tokio::test]
    async fn unexpected_native_channel_close_marks_capture_failed() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        coordinator
            .start(&system_only_configuration(), Arc::new(FakeSubmitter))
            .await
            .expect("starts");
        sender.lock().expect("fake bridge lock").take();

        timeout(Duration::from_secs(1), async {
            loop {
                if coordinator.status().expect("status").state() == AudioCaptureState::Failed {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("channel close is observed");
        coordinator.stop().await.expect("idempotent cleanup");
    }

    #[tokio::test]
    async fn submission_failure_ends_the_worker_without_exposing_audio() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        coordinator
            .start(&system_only_configuration(), Arc::new(FailingSubmitter))
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(CHUNK_SAMPLES + 1))
            .expect("callback stays non-blocking");

        timeout(Duration::from_secs(1), async {
            loop {
                if coordinator.status().expect("status").state() == AudioCaptureState::Failed {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("failure is observed");
        let status = coordinator.status().expect("privacy-safe status");
        assert_eq!(status.message(), Some("Audio submission failed."));
        coordinator.stop().await.expect("stops failed capture");
    }

    #[tokio::test]
    async fn recording_branch_writes_zero_overlap_segments_and_flushes_the_short_tail() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        let (writer, segments) = recording_writer(false);
        let recording_branch: Arc<dyn RecordingSegmentWriter> = writer;
        coordinator
            .start_with_recording(
                &system_only_configuration(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
                Some(recording_branch),
                false,
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(160_001))
            .expect("callback remains non-blocking");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame_at(1, 160_001.0 / 16_000.0))
            .expect("second callback remains non-blocking");

        coordinator.stop().await.expect("stops and flushes");
        let segments = segments.lock().expect("recording segments lock");
        assert_eq!(
            segments
                .iter()
                .map(|(index, count, _)| (*index, *count))
                .collect::<Vec<_>>(),
            vec![(0, 80_000), (1, 80_000), (2, 1)]
        );
        assert!(segments
            .iter()
            .all(|(_, _, wav)| wav.starts_with(b"RIFF") && wav.get(8..12) == Some(b"WAVE")));
        assert!(!submitted.lock().expect("submitter lock").is_empty());
    }

    #[tokio::test]
    async fn recording_failure_does_not_stop_transcription() {
        let (bridge, sender) = bridge();
        let mut coordinator = AudioCaptureCoordinator::new(bridge);
        let submitted = Arc::new(Mutex::new(Vec::new()));
        let (writer, _) = recording_writer(true);
        let recording_branch: Arc<dyn RecordingSegmentWriter> = writer;
        coordinator
            .start_with_recording(
                &system_only_configuration(),
                Arc::new(RecordingSubmitter {
                    sequences: Arc::clone(&submitted),
                }),
                Some(recording_branch),
                false,
            )
            .await
            .expect("starts");
        sender
            .lock()
            .expect("fake bridge lock")
            .as_ref()
            .expect("worker sender")
            .try_send(native_frame(80_001))
            .expect("callback remains non-blocking");
        timeout(Duration::from_secs(1), async {
            loop {
                if !submitted.lock().expect("submitter lock").is_empty() {
                    return;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("transcription continues");
        assert_eq!(
            coordinator.status().expect("status").message(),
            Some("Recording is unavailable.")
        );
        coordinator.stop().await.expect("stops");
    }
}
