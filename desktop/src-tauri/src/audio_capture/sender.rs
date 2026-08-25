//! Bounded, privacy-safe asynchronous submission of finalized WAV chunks.

use std::{
    collections::VecDeque,
    future::Future,
    pin::Pin,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Instant,
};

use tokio::{
    sync::Notify,
    task::JoinHandle,
    time::{timeout, Duration},
};

use super::wav::EncodedAudioChunk;
use crate::live_transcription::client::{
    ChunkSubmissionResult, LiveTranscriptionClient, LiveTranscriptionClientError,
};

/// A four-second chunk with one second of overlap is finalized about every
/// three seconds. Seven queued chunks cover the bounded twenty-second
/// sequential response budget, including one cadence boundary, without
/// retaining an unbounded amount of audio in memory.
pub(crate) const FINALIZED_CHUNK_QUEUE_CAPACITY: usize = 7;

/// A finalized chunk becomes eligible for submission at this instant. The
/// timestamp is private process-local observability data: it is never sent to
/// the backend or exposed through Tauri.
struct QueuedAudioChunk {
    chunk: EncodedAudioChunk,
    enqueued_at: Instant,
}

/// One FIFO entry after it has left the bounded queue.
struct DequeuedAudioChunk {
    chunk: EncodedAudioChunk,
    queue_wait_ms: u128,
    queue_depth: usize,
}

/// Private, fakeable boundary between encoded audio and the WebSocket client.
///
/// It carries the exact number of newer chunks retained by the bounded FIFO.
/// The client still retains ownership of sessions, AMCP sequence numbers, and
/// authentication material.
pub(crate) trait ChunkSubmitter: Send + Sync + 'static {
    fn submit(
        &self,
        chunk: EncodedAudioChunk,
        upstream_pending_chunks: u8,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                + Send
                + '_,
        >,
    >;
}

impl ChunkSubmitter for LiveTranscriptionClient {
    fn submit(
        &self,
        chunk: EncodedAudioChunk,
        upstream_pending_chunks: u8,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                + Send
                + '_,
        >,
    > {
        Box::pin(self.submit_wav_chunk(
            chunk.capture_started_at_seconds,
            chunk.overlap_seconds,
            upstream_pending_chunks,
            chunk.wav_payload,
        ))
    }
}

/// Bounded FIFO queue for finalized chunks awaiting sequential submission.
///
/// The native callback never waits on this queue. If the bounded queue is
/// full, the newest finalized chunk is rejected explicitly; already queued
/// speech remains in FIFO order and is never replaced.
pub(crate) struct FinalizedChunkQueue {
    chunks: Mutex<VecDeque<QueuedAudioChunk>>,
    closed: AtomicBool,
    overloaded: AtomicBool,
    notify: Notify,
}

impl FinalizedChunkQueue {
    pub(crate) fn new() -> Arc<Self> {
        Arc::new(Self {
            chunks: Mutex::new(VecDeque::with_capacity(FINALIZED_CHUNK_QUEUE_CAPACITY)),
            closed: AtomicBool::new(false),
            overloaded: AtomicBool::new(false),
            notify: Notify::new(),
        })
    }

    /// Adds one chunk without ever replacing a previously queued chunk.
    pub(crate) fn push(&self, chunk: EncodedAudioChunk) -> Result<(), FinalizedChunkQueueError> {
        if self.closed.load(Ordering::Acquire) {
            return Err(FinalizedChunkQueueError::Closed);
        }

        let mut chunks = self.chunks.lock().expect("finalized chunk queue lock");
        if self.closed.load(Ordering::Acquire) {
            return Err(FinalizedChunkQueueError::Closed);
        }
        if chunks.len() >= FINALIZED_CHUNK_QUEUE_CAPACITY {
            drop(chunks);
            let first_overload = !self.overloaded.swap(true, Ordering::AcqRel);
            #[cfg(debug_assertions)]
            if first_overload {
                eprintln!("audio-capture sender queue full");
            }
            #[cfg(not(debug_assertions))]
            let _ = first_overload;
            return Err(FinalizedChunkQueueError::Full);
        }
        chunks.push_back(QueuedAudioChunk {
            chunk,
            enqueued_at: Instant::now(),
        });
        drop(chunks);
        self.notify.notify_one();
        Ok(())
    }

    async fn next(&self) -> Option<DequeuedAudioChunk> {
        loop {
            if let Some(chunk) = self.pop() {
                return Some(chunk);
            }
            if self.closed.load(Ordering::Acquire) {
                return None;
            }
            self.notify.notified().await;
        }
    }

    fn pop(&self) -> Option<DequeuedAudioChunk> {
        let mut chunks = self.chunks.lock().expect("finalized chunk queue lock");
        let entry = chunks.pop_front();
        let depth = chunks.len();
        drop(chunks);
        let dequeued = entry.map(|entry| DequeuedAudioChunk {
            chunk: entry.chunk,
            queue_wait_ms: entry.enqueued_at.elapsed().as_millis(),
            queue_depth: depth,
        });
        dequeued
    }

    pub(crate) fn clear(&self) {
        self.chunks
            .lock()
            .expect("finalized chunk queue lock")
            .clear();
    }

    pub(crate) fn close(&self) {
        self.closed.store(true, Ordering::Release);
        self.notify.notify_waiters();
    }

    pub(crate) fn overloaded(&self) -> bool {
        self.overloaded.load(Ordering::Acquire)
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.chunks
            .lock()
            .expect("finalized chunk queue lock")
            .len()
    }
}

/// Queue admission remains non-blocking; callers decide how to surface a
/// bounded overload without exposing audio content.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizedChunkQueueError {
    Closed,
    Full,
}

/// One task owns all awaits on the live-transcription submission boundary.
pub(crate) struct ChunkSenderTask {
    queue: Arc<FinalizedChunkQueue>,
    stopped: Arc<AtomicBool>,
    failed: Arc<AtomicBool>,
    failure_notify: Arc<Notify>,
    handle: JoinHandle<()>,
}

impl ChunkSenderTask {
    pub(crate) fn start(submitter: Arc<dyn ChunkSubmitter>) -> (Self, Arc<FinalizedChunkQueue>) {
        let queue = FinalizedChunkQueue::new();
        let stopped = Arc::new(AtomicBool::new(false));
        let failed = Arc::new(AtomicBool::new(false));
        let failure_notify = Arc::new(Notify::new());
        let task_queue = Arc::clone(&queue);
        let task_stopped = Arc::clone(&stopped);
        let task_failed = Arc::clone(&failed);
        let task_failure_notify = Arc::clone(&failure_notify);

        let handle = tokio::spawn(async move {
            while !task_stopped.load(Ordering::Acquire) {
                let Some(queued_chunk) = task_queue.next().await else {
                    return;
                };
                if task_stopped.load(Ordering::Acquire) {
                    return;
                }
                let DequeuedAudioChunk {
                    chunk,
                    queue_wait_ms,
                    queue_depth,
                } = queued_chunk;
                #[cfg(not(debug_assertions))]
                let _ = (queue_wait_ms, queue_depth);
                #[cfg(debug_assertions)]
                eprintln!(
                    "live-transcription chunk submit started sequence={} queue_wait_ms={queue_wait_ms} queue_depth={queue_depth}",
                    chunk.sequence,
                );
                #[cfg(debug_assertions)]
                let sequence = chunk.sequence;
                #[cfg(debug_assertions)]
                let submission_started_at = Instant::now();
                let upstream_pending_chunks =
                    u8::try_from(queue_depth).expect("bounded queue depth fits in a u8");
                match submitter.submit(chunk, upstream_pending_chunks).await {
                    Err(_) => {
                        #[cfg(debug_assertions)]
                        {
                            let submission_elapsed_ms = submission_started_at.elapsed().as_millis();
                            eprintln!(
                                "live-transcription chunk submit failed sequence={sequence} submission_elapsed_ms={submission_elapsed_ms} queue_wait_ms={queue_wait_ms} total_elapsed_ms={}",
                                queue_wait_ms.saturating_add(submission_elapsed_ms),
                            );
                        }
                        task_failed.store(true, Ordering::Release);
                        task_failure_notify.notify_waiters();
                        task_queue.clear();
                        return;
                    }
                    Ok(_) => {
                        #[cfg(debug_assertions)]
                        {
                            let submission_elapsed_ms = submission_started_at.elapsed().as_millis();
                            eprintln!(
                                "live-transcription chunk submit completed sequence={sequence} submission_elapsed_ms={submission_elapsed_ms} queue_wait_ms={queue_wait_ms} total_elapsed_ms={}",
                                queue_wait_ms.saturating_add(submission_elapsed_ms),
                            );
                        }
                    }
                }
            }
        });

        (
            Self {
                queue: Arc::clone(&queue),
                stopped,
                failed,
                failure_notify,
                handle,
            },
            queue,
        )
    }

    /// Give an in-flight submission a short chance to finish before cancelling
    /// the task. No queued audio is drained after stop begins.
    pub(crate) async fn stop(self) {
        self.stopped.store(true, Ordering::Release);
        self.queue.close();
        self.queue.clear();
        self.queue.notify.notify_waiters();
        let mut handle = self.handle;
        if timeout(Duration::from_millis(250), &mut handle)
            .await
            .is_err()
        {
            handle.abort();
            let _ = handle.await;
        }
    }

    pub(crate) fn failed(&self) -> bool {
        self.failed.load(Ordering::Acquire)
    }

    pub(crate) fn failure_signal(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.failed)
    }

    pub(crate) fn failure_notifier(&self) -> Arc<Notify> {
        Arc::clone(&self.failure_notify)
    }
}

#[cfg(test)]
mod tests {
    use std::{
        future::Future,
        pin::Pin,
        sync::{Arc, Mutex},
        time::Duration,
    };

    use tokio::{
        sync::Semaphore,
        time::{sleep, timeout},
    };

    use super::{
        ChunkSenderTask, ChunkSubmitter, FinalizedChunkQueue, FinalizedChunkQueueError,
        FINALIZED_CHUNK_QUEUE_CAPACITY,
    };
    use crate::{
        audio_capture::wav::EncodedAudioChunk,
        live_transcription::client::{ChunkSubmissionResult, LiveTranscriptionClientError},
    };

    fn chunk(sequence: u64) -> EncodedAudioChunk {
        EncodedAudioChunk {
            sequence,
            capture_started_at_seconds: sequence as f64,
            overlap_seconds: 0.5,
            wav_payload: vec![sequence as u8],
        }
    }

    #[test]
    fn bounded_queue_preserves_fifo_and_rejects_the_newest_chunk_on_overflow() {
        let queue = FinalizedChunkQueue::new();
        for sequence in 0..FINALIZED_CHUNK_QUEUE_CAPACITY as u64 {
            assert_eq!(queue.push(chunk(sequence)), Ok(()));
        }
        assert_eq!(queue.len(), FINALIZED_CHUNK_QUEUE_CAPACITY);

        assert_eq!(
            queue.push(chunk(FINALIZED_CHUNK_QUEUE_CAPACITY as u64)),
            Err(FinalizedChunkQueueError::Full)
        );
        assert!(queue.overloaded());
        assert_eq!(queue.len(), FINALIZED_CHUNK_QUEUE_CAPACITY);

        let retained = (0..FINALIZED_CHUNK_QUEUE_CAPACITY)
            .map(|_| queue.pop().expect("retained chunk").chunk.sequence)
            .collect::<Vec<_>>();
        assert_eq!(
            retained,
            (0..FINALIZED_CHUNK_QUEUE_CAPACITY as u64).collect::<Vec<_>>()
        );
    }

    #[test]
    fn dequeue_preserves_fifo_sequence_and_records_remaining_depth() {
        let queue = FinalizedChunkQueue::new();
        assert_eq!(queue.push(chunk(20)), Ok(()));
        assert_eq!(queue.push(chunk(21)), Ok(()));

        let first = queue.pop().expect("first queued chunk");
        assert_eq!(first.chunk.sequence, 20);
        assert_eq!(first.queue_depth, 1);

        let second = queue.pop().expect("second queued chunk");
        assert_eq!(second.chunk.sequence, 21);
        assert_eq!(second.queue_depth, 0);
    }

    struct RecordingSubmitter {
        submitted: Arc<Mutex<Vec<(u64, u8)>>>,
        fail: bool,
    }

    struct GatedSubmitter {
        submitted: Arc<Mutex<Vec<(u64, u8)>>>,
        permits: Arc<Semaphore>,
    }

    impl ChunkSubmitter for GatedSubmitter {
        fn submit(
            &self,
            chunk: EncodedAudioChunk,
            upstream_pending_chunks: u8,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            let submitted = Arc::clone(&self.submitted);
            let permits = Arc::clone(&self.permits);
            Box::pin(async move {
                submitted
                    .lock()
                    .expect("gated submitter lock")
                    .push((chunk.sequence, upstream_pending_chunks));
                permits
                    .acquire()
                    .await
                    .expect("test semaphore is not closed")
                    .forget();
                Ok(ChunkSubmissionResult {
                    sequence: chunk.sequence,
                    skipped_silence: false,
                    accepted_segment_count: 0,
                    gap_reported: false,
                })
            })
        }
    }

    impl ChunkSubmitter for RecordingSubmitter {
        fn submit(
            &self,
            chunk: EncodedAudioChunk,
            upstream_pending_chunks: u8,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<ChunkSubmissionResult, LiveTranscriptionClientError>>
                    + Send
                    + '_,
            >,
        > {
            let submitted = Arc::clone(&self.submitted);
            let fail = self.fail;
            Box::pin(async move {
                submitted
                    .lock()
                    .expect("recording submitter lock")
                    .push((chunk.sequence, upstream_pending_chunks));
                if fail {
                    Err(LiveTranscriptionClientError::ProtocolFailed)
                } else {
                    Ok(ChunkSubmissionResult {
                        sequence: chunk.sequence,
                        skipped_silence: false,
                        accepted_segment_count: 0,
                        gap_reported: false,
                    })
                }
            })
        }
    }

    async fn wait_for_submission(submitted: &Arc<Mutex<Vec<(u64, u8)>>>, expected: usize) {
        timeout(Duration::from_secs(1), async {
            loop {
                if submitted.lock().expect("recording submitter lock").len() >= expected {
                    return;
                }
                sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("submission completes promptly");
    }

    #[tokio::test]
    async fn sender_submits_chunks_sequentially_in_queue_order() {
        let submitted = Arc::new(Mutex::new(Vec::new()));
        let submitter = RecordingSubmitter {
            submitted: Arc::clone(&submitted),
            fail: false,
        };
        let (sender_task, queue) = ChunkSenderTask::start(Arc::new(submitter));
        assert_eq!(queue.push(chunk(3)), Ok(()));
        wait_for_submission(&submitted, 1).await;
        assert_eq!(queue.push(chunk(4)), Ok(()));
        wait_for_submission(&submitted, 2).await;
        assert_eq!(
            *submitted.lock().expect("recording submitter lock"),
            vec![(3, 0), (4, 0)]
        );
        sender_task.stop().await;
    }

    #[tokio::test]
    async fn sender_drains_retained_fifo_chunks_after_an_explicit_overload() {
        let submitted = Arc::new(Mutex::new(Vec::new()));
        let permits = Arc::new(Semaphore::new(0));
        let (sender_task, queue) = ChunkSenderTask::start(Arc::new(GatedSubmitter {
            submitted: Arc::clone(&submitted),
            permits: Arc::clone(&permits),
        }));

        assert_eq!(queue.push(chunk(0)), Ok(()));
        wait_for_submission(&submitted, 1).await;
        for sequence in 1..=FINALIZED_CHUNK_QUEUE_CAPACITY as u64 {
            assert_eq!(queue.push(chunk(sequence)), Ok(()));
        }
        assert_eq!(
            queue.push(chunk(FINALIZED_CHUNK_QUEUE_CAPACITY as u64 + 1)),
            Err(FinalizedChunkQueueError::Full)
        );
        assert!(queue.overloaded());

        permits.add_permits(FINALIZED_CHUNK_QUEUE_CAPACITY + 1);
        wait_for_submission(&submitted, FINALIZED_CHUNK_QUEUE_CAPACITY + 1).await;
        assert_eq!(
            *submitted.lock().expect("gated submitter lock"),
            vec![
                (0, 0),
                (1, 6),
                (2, 5),
                (3, 4),
                (4, 3),
                (5, 2),
                (6, 1),
                (7, 0)
            ]
        );
        assert!(!sender_task.failed());
        sender_task.stop().await;
    }

    #[tokio::test]
    async fn sender_failure_marks_failure_and_clears_unsent_audio() {
        let submitted = Arc::new(Mutex::new(Vec::new()));
        let submitter = RecordingSubmitter {
            submitted: Arc::clone(&submitted),
            fail: true,
        };
        let (sender_task, queue) = ChunkSenderTask::start(Arc::new(submitter));
        assert_eq!(queue.push(chunk(8)), Ok(()));
        wait_for_submission(&submitted, 1).await;
        timeout(Duration::from_secs(1), async {
            while !sender_task.failed() {
                sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("failure is observed");
        assert_eq!(queue.len(), 0);
        sender_task.stop().await;
    }
}
