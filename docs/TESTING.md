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
