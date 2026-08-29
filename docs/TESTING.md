# AI Meeting Copilot

# Testing Strategy

Version: 1.0

Status: Active

---

# Purpose

Testing is a core engineering activity.

Every feature should be designed to be testable before implementation.

The objective of testing is not only to detect bugs but to ensure long-term maintainability, architectural stability, and confidence during refactoring.

---

# Testing Philosophy

The project follows these principles

* Test behavior, not implementation.
* Keep tests deterministic.
* Prefer fast tests.
* Isolate dependencies.
* Avoid flaky tests.
* Automate whenever possible.
* Tests are documentation.

---

# Testing Pyramid

```
                End-to-End
                    ▲
            Integration Tests
                    ▲
               Unit Tests
```

Most tests should be Unit Tests.

End-to-End tests should verify complete user workflows.

---

# Test Categories

## Unit Tests

Purpose

Verify a single function, class, or service in isolation.

Examples

* TranslationService
* MeetingService
* PromptBuilder
* EventPublisher
* SettingsService

Characteristics

* Fast
* Deterministic
* No network
* No database
* No filesystem (unless mocked)

---

## Integration Tests

Purpose

Verify collaboration between multiple modules.

Examples

* Service + Repository
* Repository + SQLite
* Event Bus + Subscribers
* Provider Interface + Mock Provider

Integration tests should use temporary databases and isolated resources.

---

## End-to-End Tests

Purpose

Validate complete user workflows.

Examples

* Start meeting
* Capture audio
* Generate transcript
* Translate
* Produce summary
* Save session

These tests should resemble real usage as closely as practical.

---

# AI Testing

AI systems require additional validation beyond traditional software testing.

Areas to evaluate

* prompt correctness
* response structure
* hallucination resistance
* latency
* determinism (where possible)
* failure handling

---

# Prompt Regression Testing

Prompts evolve over time.

Every prompt modification should be evaluated against a reference dataset.

Validation criteria

* JSON schema compliance
* required fields present
* no unexpected fields
* acceptable response quality

Prompt regressions should be detected before release.

---

# RAG Evaluation

Evaluate retrieval independently from generation.

Recommended metrics

* Context Precision
* Context Recall
* Answer Relevancy
* Faithfulness

The retrieval pipeline should be measurable and repeatable.

---

# Speech Recognition Testing

Validate

* transcription accuracy
* multilingual speech
* silence handling
* VAD segmentation
* timestamp ordering

Use representative audio samples rather than synthetic data alone.

## Opt-in Local Faster-Whisper Smoke Test

The real local Faster-Whisper smoke test is skipped by default and never runs in
normal CI. It uses a developer-provided WAV file; do not commit audio or model
files.

```bash
AI_MEETING_COPILOT_RUN_LOCAL_AI_TESTS=1 \
AI_MEETING_COPILOT_TEST_WAV_PATH=/absolute/path/sample.wav \
AI_MEETING_COPILOT_SPEECH_TO_TEXT_PROVIDER=faster-whisper \
uv run pytest -m local_ai -v
```

The first enabled run may download the configured model and can take time on
CPU.

## Opt-in Local Ollama Smoke Tests

The local Ollama capability smoke tests are skipped by default and never run in
normal CI. Ollama must already be running and the configured capability models
must already be pulled.

```bash
AI_MEETING_COPILOT_RUN_LOCAL_AI_TESTS=1 \
uv run pytest -m local_ai -v
```

These tests may be slow on CPU. They use concise synthetic inputs and do not
print generated content.

## Opt-in Desktop Assist Mode Smoke Validation

Desktop Assist Mode validation is manual and opt-in because macOS capture
permissions and display selection require developer interaction. Normal CI
never runs it.

Start only after Faster-Whisper is configured locally, Ollama is running with
the configured models already pulled, and macOS Screen Recording/System Audio
and Microphone permissions have been granted:

```bash
AI_MEETING_COPILOT_RUN_LOCAL_ASSIST_TESTS=1 \
AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS=1 \
npm run native-audio:smoke
```

In the desktop app, start the backend, enter a Meeting name, start a session,
select capture sources, and begin capture. Confirm that a finalized transcript
row appears, then that its translation (and optional simplification) attaches
to the same row and reply suggestions appear. Do not print or copy transcript,
prompt, token, or audio data during validation. End the session, stop capture,
and quit the app; this stops the WebSocket and sidecar while leaving the
persisted Meeting transcript intact.

## Opt-in Live-Audio Throughput Comparison

Use this manual macOS-only diagnostic when a real-time capture falls behind.
It emits only fixed metric labels, counts, queue depths, and monotonic elapsed
times; it never emits audio, transcript text, prompts, provider responses,
tokens, device identifiers, or endpoint details.

Launch the development app from `desktop/` with the opt-in environment flag:

```bash
AI_MEETING_COPILOT_THROUGHPUT_DIAGNOSTICS_ENABLED=1 \
AI_MEETING_COPILOT_LOG_LEVEL=INFO \
npm run tauri:dev
```

Replay the same local audio workload through System Audio for at least three
minutes in an A-B-A sequence: Assist disabled, then Assist with Turkish
translation enabled while simplification and reply coaching are explicitly
disabled, then Assist disabled again. Keep the backend process running across
all three runs, and discard the initial warm-up interval before comparing
results.

Compare the terminal's `amcp-throughput` lines with the Rust capture lines:

* `stt_completed elapsed_ms` for median and p95 STT latency;
* `ollama_completed elapsed_ms`, `assist_capability_completed`, and
  `assist_segment_completed` for Assist request and total work duration;
* sender queue depth/wait and chunk cadence for backlog growth; and
* native callback accepted/full-drop counts with processing input duration for
  capture throughput.

For a Turkish-translation failure, use the same opt-in stream to identify the
safe boundary without exposing text or provider responses. A successful
translation emits `assist_translation_started`, `ollama_started`,
`ollama_response_received`, `assist_translation_response_received`, and
`assist_translation_update_ready`. A failure carries only one fixed reason on
`ollama_completed` or a response-validation stage: `connection_failed`,
`timeout`, `http_status_error`, `request_failed`, `model_unavailable`,
`malformed_response`, `empty_response`, `response_validation_failed`, or
`provider_internal_error`.

For every `stt_completed` line, sum `processed_audio_ms` and divide by the
run's wall-clock duration to obtain processed-audio-seconds per wall-clock
second. The same line carries `ollama_active_requests`, allowing the B run's
STT latency to be grouped safely into Ollama-idle (`0`) and Ollama-active
(`>0`) samples. The total WAV duration (`audio_duration_ms`) is also present
for diagnosing overlap versus new audio; it must not be summed as source audio
because overlapping chunks intentionally repeat audio.

For cold-start analysis, preserve the first chunk rather than discarding it.
`stt_provider_started first_request=yes`, `stt_model_ready model_loaded=yes`,
and `model_load_ms` identify the one lazy model-construction call. Compare
that request with later `first_request=no` entries. The installed
Faster-Whisper implementation performs WAV decode, feature extraction, and
token setup synchronously in `model.transcribe()`, reported as
`audio_decode_conversion_ms`; its lazy segment iterator performs generation,
reported as `stt_inference_ms`. The route also emits `entry_monotonic_ms`,
`frame_parse_ms`, result
serialization/send `elapsed_ms`, and `backend_total_ms`; all values remain
structural and content-free.

Do not treat model residency or a single slow chunk as proof of contention.
Only a repeatable A-B-A comparison that shows a material Assist-on difference
can establish local resource contention. Normal CI never enables these
diagnostics.

## Offline STT and Turkish Translation Quality Baseline

Quality evaluation is deliberately separate from the live WebSocket, Assist,
and throughput-diagnostic paths. It accepts developer-supplied reference text
and optional local WAV paths, so private recordings are never added to Git or
written to normal production telemetry.

Create a local JSON fixture outside the repository when it contains private
audio or conversation text:

```json
{
  "cases": [
    {
      "id": "local-axa-sample",
      "reference_de": "Expected German passage.",
      "reference_tr": "Optional human Turkish reference.",
      "audio_path": "local-axa-sample.wav",
      "live_segments": ["Observed live fragment one", "Observed live fragment two"],
      "evaluation_terms": ["AXA", "Data Scientist", "Heiko Fleming"]
    }
  ]
}
```

Run only the measurements needed for the baseline, from the repository root:

```bash
uv run python backend/scripts/run_quality_baseline.py \
  --fixture /local/path/quality-cases.json \
  --stt --translation \
  --translation-timeout-seconds 180
```

The command constructs the same configured Faster-Whisper and Ollama provider
adapters as the local Container, but it does not create a Meeting, connect a
WebSocket, enqueue Assist work, or emit `amcp-throughput` diagnostics. It
prints the explicitly supplied evaluation text only to the invoking
developer's terminal. For `--stt`, provide a PCM16 mono 16 kHz WAV. The
offline loader reproduces the current live profile: complete four-second WAV
chunks with one second of overlap; it deliberately omits an incomplete trailing
chunk, matching the live finalized-chunk policy.

The report keeps raw and normalized German text separate, reports word error
rate plus substitutions/deletions/insertions, and shows explicitly supplied
evaluation vocabulary. Translation is measured independently using correct
German reference text, end-to-end from STT output, current/live fragments, and
bounded coherent German sentence groups. The offline command prints fixed-label
per-unit progress and records timeouts/failures without raw provider details;
one failed unit does not discard completed independent units. Its default
developer-only per-unit Ollama timeout is 180 seconds and is implemented by a
separate offline client, so it never changes the production setting. The
optional `--include-whole-passage` flag runs a separate one-request experiment;
it is intentionally not part of the normal clean-input baseline. Structural
categories (`source_copy`,
`non_turkish_script`, `markup_leak`, `special_token_leak`, `empty`, and
`unexpected_language`) identify obvious invalid output only; they are not a
semantic Turkish-quality score and do not filter production results.

### Offline Translation Prompt Experiments

For a private fixture that also contains a `translation_benchmark.units` array
of manually aligned German/Turkish units, compare the current prompt with
offline-only candidate prompts using the already configured local Ollama model:

```bash
uv run python backend/scripts/run_translation_quality_experiments.py \
  --fixture /local/path/quality-cases.json \
  --translation-timeout-seconds 180 \
  --report /local/path/translation-experiment-report.txt
```

The benchmark runner never changes production prompts, model selection,
generation settings, provider timeout settings, or Assist behaviour. It runs
four bounded baseline variants: the current structured prompt, a stricter
structured prompt, a stricter structured prompt with explicitly required terms,
and a plain-text control. Quality F adds two separately selected offline
structured variants: `mandatory_preservation_structured` applies a generic
character-for-character preservation rule, while
`checklist_preservation_structured` supplies only benchmark terms present in
the current source unit. The report additionally shows a derived
MUST-PRESERVE subset metric for names, organisations, locations, and fixed
technical labels; the original all-terms metric remains unchanged for
comparability. Progress contains fixed variant/unit labels only; the
optional report contains the deliberately supplied benchmark text for human
comparison. `structurally_valid` means only that the output passed objective
format/script/source-copy checks. `semantically_acceptable` is intentionally
not inferred: every output remains `not_reviewed` until a human evaluates it
against the aligned Turkish reference.

### Offline Deterministic Terminology Protection

Quality G evaluates terminology protection independently of the live
translation provider. It replaces only the existing Quality F MUST-PRESERVE
benchmark terms present in a source unit with unique opaque
`__AMCP_TERM_###__` placeholders before the local Aya request. A result is
restored only when every expected placeholder appears exactly once and no
unknown or modified placeholder is present; otherwise it fails closed without
guessing a term. This is deliberately an offline measurement, not production
post-processing.

```bash
uv run python backend/scripts/run_terminology_protection_experiment.py \
  --fixture /local/path/quality-cases.json \
  --translation-timeout-seconds 180 \
  --report /local/path/terminology-protection-report.txt
```

### Priority-scheduler D validation

After the translation priority scheduler is enabled, run a D comparison under
the same conditions as B: System Audio on, Microphone off, Assist and Turkish
translation on, simplification and reply coaching off, the same local Ollama
and Faster-Whisper settings, the same audio workload, and approximately the
same duration. Do not alter chunking, queues, or timeouts between B, C, and D.

The scheduler emits only structural states on
`assist_translation_scheduler`. During continuous STT,
`active_translation_count` must be at most one and
`pending_translation_count` must be at most one. `busy`, `coalesced`, and
`cancelled` mean a best-effort translation was deferred, superseded, or
preempted; its transcript row remains correct and receives a terminal generic
unavailable state rather than content from another segment.

Compare D against B and C using STT/result latency over time, sender
`queue_wait_ms`, queue depth, those scheduler counts, and native full-drop
counts. The success criterion is that Assist-on transcription backlog recovers
to zero rather than trends upward, with no source-audio loss caused by a full
native-frame channel. This diagnostic does not prove OS-level cancellation of
already-started local model work; note any short residual Ollama load after a
preemption separately.

### Translation-admission E validation

Run E with the same conditions as D. The scheduler now derives a conservative
translation admission forecast from the shortest of its four most recent STT
start intervals and the slowest of its four most recent successful translation
durations. It does not use a configured chunk-size assumption. A first
translation may be a probe because no measured translation duration exists
yet.

For `assist_translation_scheduler state=started|deferred`, compare the closed
`admission_reason`, `estimated_idle_window_ms`, and, after a successful
translation exists, `estimated_translation_duration_ms`. In a steady speech
cadence that cannot fit the measured translation duration, E should show
`deferred` rather than repeated started/cancelled work. During a natural pause,
an `extended_idle` admission may run the newest pending translation. Continue
to require active/pending counts no greater than one, zero native full drops,
and sender queue wait/depth that recover to zero. An unexpected STT arrival
may still safely cancel an admitted translation; it must not increase STT
latency or attach content to a different transcript row.

### Extended-idle F validation

Run F under the same workload and settings as E. The extended-idle fallback
must not admit merely because the earliest predicted STT boundary passes. It
waits through the bounded observed cadence-jitter margin, calculated as the
difference between the longest and shortest recent STT-start intervals, or
the measured translation duration, whichever is larger. The structural
`extended_idle_margin_ms` field appears with scheduler admission metrics so
this behavior can be verified without exposing user content.

For an insufficient `predicted_window`, confirm that no
`started admission_reason=extended_idle` line appears before the predicted
boundary plus `extended_idle_margin_ms`. Pause the source audio beyond that
derived boundary to permit the newest pending translation. Resume during an
active translation to confirm cancellation remains safe, while sender queue
wait/depth, STT latency, and native drop counts remain at their D/E healthy
levels.

### Backlog-aware G validation

Run G with the same System Audio-only AXA workload and model settings as F.
Each binary audio frame now carries only the current post-dequeue depth of the
desktop's already-bounded sender FIFO (`upstream_pending_chunks`, an unsigned
8-bit structural count). The backend does not depend on the desktop queue's
capacity: it only treats a nonzero count as known STT work that must retain
priority. This field never contains audio, transcript, device, path, or model
data.

During a slow STT outlier, a latest pending translation must emit
`state=deferred admission_reason=upstream_backlog` with a nonzero
`upstream_pending_stt_chunks` count. It must not emit
`state=started admission_reason=extended_idle` until the serialized sender
backlog reaches zero and the existing predicted-window or genuine
extended-idle condition separately permits admission. Confirm active and
logical pending translation counts stay at most one, and that an unexpected
new STT start still preempts an active translation. The outer Assist queue is
independently bounded and remains intentionally unchanged in G because it
also preserves ordering for the other Assist capabilities.

### Translation-only Assist outer-queue H validation

Run H with the same G settings: System Audio on, Microphone off, Assist and
Turkish translation on, simplification and reply coaching off. Translation-only
sessions use a one-slot latest-value pending mailbox in front of the existing
Assist worker. A newly accepted segment replaces only an older *not yet
dequeued* translation-only segment; the worker's in-flight operation and all
Task G STT-priority checks remain unchanged. Mixed-capability sessions retain
the existing bounded segment FIFO so reply coaching and simplification keep
their ordering semantics.

For sustained speech, `assist_enqueued queue_depth` should remain at most one
for waiting translation-only work, rather than staying at eight. Each
replacement emits the content-free structural diagnostic
`assist_outer_queue_coalesced capability=translation replaced_count=1
queue_depth=1`. After a genuine Task G admission opportunity, the latest
segment should be the one that emits `state=started`, then a translation-ready
update. Superseded entries that never reached a processing state deliberately
produce no user-facing terminal update; their replacement is observable only
through the closed coalescing metric. Confirm a stop during a burst clears the
mailbox promptly, and do not apply this expectation to sessions with reply
coaching or simplification enabled.

---

# Translation Testing

Verify

* semantic accuracy
* language detection
* formatting preservation
* streaming consistency

Tests should include domain-specific terminology.

---

# Repository Testing

Every repository should have tests covering

* create
* read
* update
* delete
* transactions
* rollback
* constraints

Repositories should be tested against a real SQLite database where practical.

---

# Event Bus Testing

Verify

* event publication
* subscriber invocation
* event ordering
* duplicate handling
* failure isolation

Subscribers should not interfere with one another.

---

# API Testing

REST endpoints should verify

* validation
* authentication (future)
* response schema
* status codes
* error handling

WebSocket tests should verify

* connection lifecycle
* streaming
* reconnection
* malformed messages
* graceful shutdown

---

# Continue in Part 2

The next section covers

* Performance Testing
* Load Testing
* Security Testing
* Test Data Strategy
* Mocking
* CI/CD Testing
* Coverage Goals
* Test Organization
* Definition of Done
* Testing Checklist
# Performance Testing

Performance testing ensures the application remains responsive under realistic workloads.

Primary metrics

* API latency
* WebSocket latency
* Speech-to-text latency
* Translation latency
* Summary generation time
* Memory usage
* CPU usage
* GPU utilization (when applicable)

Performance targets should be measured on representative hardware.

---

# Load Testing

Although the application targets a single desktop user, load testing remains valuable.

Scenarios

* Long meetings (2–4 hours)
* High transcript volume
* Large document collections
* Continuous AI requests
* Large RAG indexes

The application should remain stable without memory leaks or significant degradation.

---

# Stress Testing

Stress testing identifies system limits.

Examples

* Extremely large transcript buffers
* Thousands of indexed document chunks
* Rapid event generation
* Simultaneous AI requests
* Repeated provider failures

The goal is graceful degradation rather than perfect operation.

---

# Security Testing

Security testing should verify

* input validation
* SQL injection resistance
* path traversal protection
* secret handling
* configuration safety
* dependency vulnerabilities

Sensitive information must never be exposed in logs or error messages.

---

# Test Data Strategy

Use realistic but non-sensitive data.

Recommended datasets

* multilingual meeting transcripts
* technical discussions
* business conversations
* product documentation
* legal documents (synthetic)
* financial reports (synthetic)

Never commit confidential or personally identifiable information.

---

# Mocking Strategy

Mock external dependencies whenever possible.

Examples

* LLM providers
* network requests
* cloud storage
* authentication services
* external APIs

Do not mock the functionality being tested.

---

# Test Organization

Suggested structure

```text
tests/
├── unit/
├── integration/
├── e2e/
├── ai/
├── rag/
├── performance/
├── fixtures/
└── data/
```

Each category should remain independent.

---

# Fixtures

Reusable fixtures should provide

* temporary databases
* sample meetings
* transcript examples
* AI provider mocks
* document collections
* configuration objects

Fixtures should be deterministic and easy to understand.

---

# Continuous Integration

Backend CI runs on pushes, pull requests, and manual dispatch using Python 3.12
and uv. The workflow synchronizes the locked dependency set before running:

```bash
uv run ruff check backend tests
uv run black --check backend tests
uv run mypy backend/app
uv run pytest
```

The test configuration discovers both repository-level tests and backend tests.
The bootstrap smoke test establishes a passing collection baseline; future
features must add focused unit, integration, or end-to-end coverage as needed.

Long-running performance tests may execute on a scheduled basis rather than every commit.

---

# Coverage Goals

Coverage is a useful indicator, not a goal by itself.

Recommended targets

| Area                | Target |
| ------------------- | -----: |
| Core Business Logic |  ≥ 90% |
| Repositories        |  ≥ 90% |
| AI Orchestration    |  ≥ 85% |
| API Layer           |  ≥ 80% |
| Utilities           |  ≥ 90% |

Coverage should not encourage meaningless tests.

---

# Regression Testing

Regression tests should be added for every production bug.

Each bug should include

* reproduction case
* failing test
* implementation fix
* passing regression test

This prevents the same issue from reappearing.

---

# AI Evaluation Pipeline

The AI subsystem should be evaluated continuously.

Recommended evaluation dimensions

* Context Precision
* Context Recall
* Answer Relevancy
* Faithfulness
* Response latency
* Structured output validity

Evaluation datasets should evolve alongside the application.

---

# Release Testing

Before each release verify

* application startup
* database migrations
* meeting lifecycle
* speech recognition
* translation
* summary generation
* RAG retrieval
* export functionality
* settings persistence

A release should not proceed if critical workflows fail.

---

# Definition of Done

A feature is complete only if

* implementation is finished
* unit tests pass
* integration tests pass
* documentation is updated
* architecture remains consistent
* code review is completed
* CI pipeline succeeds

Testing is part of development, not a separate phase.

---

# Testing Checklist

Before merging, confirm

* Code builds successfully.
* All automated tests pass.
* No flaky tests were introduced.
* Logging is appropriate.
* Error handling is covered.
* Documentation reflects behavior.
* Performance remains acceptable.
* No sensitive data is committed.

---

# Testing Summary

The testing strategy emphasizes

* correctness
* reliability
* maintainability
* reproducibility
* automation
* measurable AI quality

Testing is considered a first-class engineering activity and should evolve together with the architecture.

---

# macOS Native Audio Capture Smoke Validation

Run this manually on macOS 15 or later with a development build. Grant or deny Screen Recording and Microphone permission deliberately, then verify that capture starts only when the required permissions are authorized. Repeat with a selected display, the default microphone, and an explicitly selected microphone. Confirm that start and stop complete cleanly and that no raw audio, device name, or sample content is printed or persisted.

This is intentionally a local manual check; automated tests use fake native callbacks and never start a real ScreenCaptureKit stream.

---

# macOS Capture Command Flow

For a local macOS validation, start the desktop app, start the backend, and connect live transcription. Create an active Meeting and live-transcription session using the existing internal or test workflow. Request capture permissions, select a display and optional microphone, then start capture and confirm the status becomes `capturing`. Stop capture and confirm it returns to `stopped` without stopping the backend or disconnecting the WebSocket.

Do not record or print raw audio, transcripts, tokens, or native error details during this check.

---

# Opt-in Native Live-Transcription Smoke Test

The full native capture path is a **manual macOS smoke test**. It is never run
by normal CI: its Rust preflight is marked `#[ignore]` and the launcher refuses
to run unless `AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS=1` is set.

Prerequisites:

* macOS 15 or later;
* Screen Recording/System Audio permission for the desktop app;
* Microphone permission when microphone capture is enabled;
* a locally available Faster-Whisper model and a launchable Python sidecar;
* an active Meeting and active live-transcription session, created through the
  existing internal or test workflow.

Start the opt-in flow with:

```bash
AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS=1 \
npm run native-audio:smoke
```

In the app, start the backend, connect live transcription, request permissions,
select a display and optional microphone, then start capture. Capture for 8–12
seconds, stop capture, and end the live-transcription session. Verify through
the existing persistence test boundary that at least one finalized, non-blank
transcript entry was saved with ordered UTC timestamps.

Expected visible behavior is `capturing` followed by `stopped`; no transcript,
audio bytes, token, session identifier, or native error detail is displayed.
Closing the Tauri app stops capture before the WebSocket and sidecar are shut
down. If a run is interrupted, close the desktop app; do not retain or write
raw audio/WAV data. The deterministic Rust tests cover the non-permission
queue, encoding, submission, and cleanup boundaries separately.

---

# Local Recording and Playback Smoke Validation

Run this manual macOS validation only with local services. Start Ollama and a
configured local Faster-Whisper backend, then launch the Tauri desktop app.
Grant the required Screen Recording/System Audio and Microphone permissions.

1. Start the backend, create and start a Meeting, then start the live session.
2. Enable recording, explicitly confirm consent, select capture sources, and
   capture long enough to create multiple recording segments.
3. Stop capture and end the Meeting. Open **History** and confirm the complete
   original transcript remains available.
4. Generate or reopen the Turkish translation and Meeting Review.
5. Play, pause, resume, and stop the recording. Seek with the progress control
   and by selecting a transcript row; verify the highlight and follow control.
6. Quit and restart the app, then reopen the Meeting and confirm the persisted
   transcript, translation, review, and playable recording metadata remain
   readable.

Do not print or copy transcript, audio, token, key, path, prompt, or provider
response data while validating. If capture reports gaps, playback remains
available but exact transcript synchronization and transcript-row seeking are
intentionally unavailable. Normal CI does not run this permission-dependent
manual flow.

---

# Offline Quality I M2M100 Benchmark

Quality I is a developer-only German-to-Turkish benchmark for the approved
`facebook/m2m100_418M` dedicated MT candidate. It is not part of the live
Assist pipeline and must use an explicitly supplied private fixture and model
directories outside the repository.

Prepare the pinned public snapshot and CTranslate2 int8 artifact:

```bash
uv run python backend/scripts/prepare_m2m100_ct2.py \
  --model-directory /private/tmp/amcp-quality-i/m2m100-source \
  --ct2-directory /private/tmp/amcp-quality-i/m2m100-ct2
```

Then run the private 20-unit sentence/context benchmark and its separate
live-style fragment section:

```bash
uv run python backend/scripts/run_m2m100_quality_benchmark.py \
  --fixture /private/tmp/amcp-quality-a-edeka-interview.json \
  --model-directory /private/tmp/amcp-quality-i/m2m100-source \
  --ct2-directory /private/tmp/amcp-quality-i/m2m100-ct2 \
  --report /private/tmp/amcp-quality-i-report.txt
```

The result is intentionally a private developer report: it contains supplied
source/reference/output text for manual semantic review. Normal tests use only
synthetic fixtures; they never download M2M100, load CTranslate2 model weights,
or require an Ollama/Faster-Whisper service. The baseline is CPU int8 with
deterministic `beam_size=1`; it performs no prompt tuning, glossary handling,
or production configuration changes.

---

# Offline Quality J Sentence-aware Translation Feasibility

Quality J replays only the private fixture's existing `live_segments` through
the already installed `aya-expanse:8b` model. It compares current
single-segment requests, deterministic sentence-boundary batching, and a
literal two-consecutive-segment context policy without importing any code into
the live Assist, scheduler, or WebSocket paths.

```bash
uv run python backend/scripts/run_sentence_aware_translation_feasibility.py \
  --fixture /private/tmp/amcp-quality-a-edeka-interview.json \
  --report /private/tmp/amcp-quality-j-report.txt \
  --translation-timeout-seconds 180 \
  --segment-cadence-ms 3000
```

The cadence is an offline replay parameter only. The report includes model
inference timing and first-segment-visible latency, which adds the time spent
waiting for a batch to become eligible. Its STT-priority admission count is an
optimistic upper bound: continuous live STT can still defer or preempt an Aya
request. The report remains in private temporary storage because it contains
developer-supplied transcript and translation text.

---

# Offline Quality K STT-to-Translation Attribution

Quality K compares the same defensibly aligned speech unit in two forms:
unchanged human-reference German and unchanged live-STT German. It uses the
same offline Aya preservation-structured configuration as Quality E and never
imports into live STT, Assist, scheduling, or production telemetry.

Create the alignment manifest and report outside the repository because they
may identify private transcript segments. Each pair explicitly names the
private fixture's reference-unit IDs and live-segment IDs. A repair, when
present, is one exact evaluation-only substitution for a controlled
counterfactual; it is never production terminology correction.

```bash
uv run python backend/scripts/run_stt_translation_attribution.py \
  --fixture /local/path/quality-cases.json \
  --alignment /local/path/quality-k-alignment.json \
  --report /local/path/quality-k-report.txt \
  --translation-timeout-seconds 180
```

The private report includes deliberately supplied reference German/Turkish,
actual STT German, both Aya outputs, and optional single-error repair output
for manual attribution as `translation_only`, `stt_propagated`, `amplified`,
`robust`, or `inconclusive`. The tool makes no automatic semantic-quality
claim, does not rewrite live STT before its primary translation, has bounded
sequential requests, and emits only fixed phase/index progress to the normal
terminal.

---

# Offline Quality L Faster-Whisper Prompt and Context Replay

Quality L is a private, offline comparison of the current live STT profile
against three bounded alternatives: a short technical-vocabulary
`initial_prompt`, the immediately preceding accepted chunk as bounded context,
and their combination. It does not change the live provider configuration,
model, chunk size, deduplicator, scheduling, or telemetry.

Supply an explicit private PCM16 mono 16 kHz WAV and write the resulting report
outside the repository:

```bash
uv run python backend/scripts/run_stt_quality_experiments.py \
  --fixture /local/path/quality-cases.json \
  --case-id local-case-id \
  --audio /local/path/private-recording.wav \
  --report /local/path/quality-l-report.txt
```

The baseline sends exactly the current adapter arguments: configured
`beam_size`, configured `vad_filter`, and `language="de"`; it deliberately
leaves Faster-Whisper's `condition_on_previous_text` at the provider default.
All variants replay the same completed four-second WAV chunks at the current
one-second overlap and use the existing `TranscriptDeduplicator`. Vocabulary
is bounded to 480 prompt characters; context is only the previous accepted
chunk and is bounded to 320 characters. The report includes WER, word edits,
term/numeric-token presence, per-chunk and aggregate latency/RTF, and raw
developer-supplied output for manual hallucination/repetition review.

Quality L does not infer semantic translation quality or automatically align a
new STT run to Quality K pairs. Any downstream Aya comparison must use an
explicit new private alignment manifest after a human verifies that the new
chunk output represents the same underlying speech.

---

# Offline Quality M Bounded Prompt and Context Comparison

Quality M reuses Quality L's unchanged `previous_context` (C) and broad
`vocabulary_and_context` (D) controls. It adds only two predefined offline
conditions: the Quality K failure-derived 13-term critical vocabulary with the
existing 320-character accepted-context suffix (E), and the identical critical
vocabulary with a 160-character suffix (F). The values are fixed before a run;
they are not tuned per recording.

```bash
uv run python backend/scripts/run_stt_quality_experiments.py \
  --fixture /local/path/quality-cases.json \
  --case-id local-case-id \
  --audio /local/path/private-recording.wav \
  --report /local/path/quality-m-report.txt \
  --quality-m
```

The report adds per-chunk prompt/vocabulary/context character counts, chunk RTF
percentiles, tail-latency counts at 4/8/15/30 seconds, and deduplication totals.
It remains private developer output; no result is used to modify the live STT
configuration.

---

# Offline Quality N Faster-Whisper Capacity Replay

Quality N isolates model capacity from all Quality L/M prompt changes. It runs
only the unchanged context-only C condition: four-second PCM16 mono 16 kHz
chunks, one-second overlap, forced German, configured beam size and VAD state,
the current `TranscriptDeduplicator`, no vocabulary prompt, and the
320-character previous accepted-context suffix. The selected model is supplied
only through the process environment; it does not alter the persisted or live
application configuration.

```bash
AI_MEETING_COPILOT_FASTER_WHISPER_MODEL=medium \
uv run python backend/scripts/run_stt_quality_experiments.py \
  --fixture /local/path/quality-cases.json \
  --case-id local-case-id \
  --audio /local/path/private-recording.wav \
  --report /local/path/quality-n-medium-report.txt \
  --quality-n
```

The private report separates model-load time and first-chunk latency from warm
chunks, records the process RSS high-water mark after model loading, and
includes warm latency/RTF percentiles and tail counts. It must be compared to
an existing C control produced from the same private recording. This command
can cause Faster-Whisper to download a model if the named artifact is absent;
verify the local cache and obtain approval before using a new model name.

---

# Offline Quality O Timestamp-Aligned Gold STT Evaluation

Quality O is a developer-only foundation for private, human-verified German
gold references. It is deliberately separate from live STT and from full
four-second/one-second-overlap pipeline replay: only verified,
evaluation-eligible gold intervals contribute to its absolute WER/CER.

Store the real audio, gold reference JSONL, model-prediction JSONL, and reports
outside Git. Gold and prediction records are strictly separate:

```text
private/gold.jsonl + private/predictions.jsonl -> local Quality O report
```

Each gold JSONL record has an opaque `audio_id`, interval `[start_ms, end_ms)`,
human German text, a review state, eligibility flag, tags, explicit term and
number annotations, and bounded review metadata. There is no audio path in the
schema. Prediction JSONL records contain only an opaque audio/segment identity,
run/model labels, and German hypothesis text; they cannot contain gold text.

Primary metrics include only `reference_state="verified"` and
`evaluation_eligible=true` intervals. `uncertain`, `unintelligible`,
`partial_word`, and `interrupted` intervals remain documented but excluded from
absolute WER/CER. NFC normalization, case folding, whitespace/punctuation
handling, and tokenization are deterministic and model-independent; no number
word conversion, technical-term correction, or known-model-confusion repair is
applied to general WER.

Technical terms and numbers are scored only when explicit gold annotations
identify their occurrence. Numeric value equivalents are annotation supplied;
technical identifiers such as `BM25` are never inferred to be the standalone
number `25`. The Phase 1 foundation has no translation-impact evaluation and
does not run models, replay audio, or change live STT behavior.

Normal tests use synthetic JSONL only. Never commit private interview audio,
transcripts, gold records, predictions, alignment manifests, reviewer notes, or
reports containing supplied text.

---
