//! Bounded, privacy-safe asynchronous submission of finalized WAV chunks.

use std::{
    collections::VecDeque,
    future::Future,
    pin::Pin,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
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

/// Private, fakeable boundary between encoded audio and the WebSocket client.
///
/// It deliberately accepts only an encoded chunk. The client retains ownership
/// of sessions, AMCP sequence numbers, and authentication material.
pub(crate) trait ChunkSubmitter: Send + Sync + 'static {
    fn submit(
        &self,
        chunk: EncodedAudioChunk,
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
            chunk.wav_payload,
        ))
    }
}

/// Capacity-one queue that discards the oldest unsent chunk under overload.
pub(crate) struct FinalizedChunkQueue {
    chunks: Mutex<VecDeque<EncodedAudioChunk>>,
    closed: AtomicBool,
    dropped: AtomicBool,
    notify: Notify,
}

impl FinalizedChunkQueue {
    pub(crate) fn new() -> Arc<Self> {
        Arc::new(Self {
            chunks: Mutex::new(VecDeque::with_capacity(1)),
            closed: AtomicBool::new(false),
            dropped: AtomicBool::new(false),
            notify: Notify::new(),
        })
    }

    /// Returns whether an older, unsent chunk was discarded.
    pub(crate) fn push(&self, chunk: EncodedAudioChunk) -> bool {
        if self.closed.load(Ordering::Acquire) {
            return false;
        }

        let mut chunks = self.chunks.lock().expect("finalized chunk queue lock");
        let dropped = if chunks.len() == 1 {
            chunks.pop_front();
            true
        } else {
            false
        };
        chunks.push_back(chunk);
        if dropped {
            self.dropped.store(true, Ordering::Release);
        }
        drop(chunks);
        self.notify.notify_one();
        dropped
    }

    async fn next(&self) -> Option<EncodedAudioChunk> {
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

    fn pop(&self) -> Option<EncodedAudioChunk> {
        self.chunks
            .lock()
            .expect("finalized chunk queue lock")
            .pop_front()
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

    pub(crate) fn dropped(&self) -> bool {
        self.dropped.load(Ordering::Acquire)
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.chunks
            .lock()
            .expect("finalized chunk queue lock")
            .len()
    }
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
                let Some(chunk) = task_queue.next().await else {
                    return;
                };
                if task_stopped.load(Ordering::Acquire) {
                    return;
                }
                if submitter.submit(chunk).await.is_err() {
                    task_failed.store(true, Ordering::Release);
                    task_failure_notify.notify_waiters();
                    task_queue.clear();
                    return;
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

    use tokio::time::{sleep, timeout};

    use super::{ChunkSenderTask, ChunkSubmitter, FinalizedChunkQueue};
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
    fn capacity_is_one_and_preserves_the_newest_chunk() {
        let queue = FinalizedChunkQueue::new();
        assert!(!queue.push(chunk(1)));
        assert_eq!(queue.len(), 1);
        assert!(queue.push(chunk(2)));
        assert_eq!(queue.len(), 1);
        assert!(queue.dropped());
        assert_eq!(queue.pop().expect("newest chunk").sequence, 2);
    }

    struct RecordingSubmitter {
        submitted: Arc<Mutex<Vec<u64>>>,
        fail: bool,
    }

    impl ChunkSubmitter for RecordingSubmitter {
        fn submit(
            &self,
            chunk: EncodedAudioChunk,
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
                    .push(chunk.sequence);
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

    async fn wait_for_submission(submitted: &Arc<Mutex<Vec<u64>>>, expected: usize) {
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
        assert!(!queue.push(chunk(3)));
        wait_for_submission(&submitted, 1).await;
        assert!(!queue.push(chunk(4)));
        wait_for_submission(&submitted, 2).await;
        assert_eq!(
            *submitted.lock().expect("recording submitter lock"),
            vec![3, 4]
        );
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
        assert!(!queue.push(chunk(8)));
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
