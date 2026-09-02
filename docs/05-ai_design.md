# AI Meeting Copilot

# AI Design Specification

Version: 1.0

Status: Active

Last Updated: 2026

---

# Purpose

This document defines the artificial intelligence architecture used throughout AI Meeting Copilot.

Its goal is to establish a consistent, modular, provider-agnostic AI system that is privacy-first, low-latency, and maintainable.

This document serves as the technical reference for all AI-related implementations.

---

# Design Principles

The AI system follows these principles:

- Human-first
- AI assists, never replaces
- Local-first whenever possible
- Privacy by design
- Provider agnostic
- Modular pipelines
- Streaming-first
- Observable
- Testable
- Configurable

Every AI component must comply with these principles.

---

# AI Responsibilities

The AI system is responsible for:

- Speech recognition
- Language detection
- Translation
- German simplification
- Question detection
- Reply coaching
- Meeting summarization
- Action item extraction
- Vocabulary extraction
- Knowledge retrieval
- Context management
- Learning assistance

The AI system is **not** responsible for making decisions on behalf of the user.

---

# High-Level AI Architecture

```
Microphone
    │
    ▼
Voice Activity Detection
    │
    ▼
Speech Recognition
    │
    ▼
Transcript Buffer
    │
    ▼
Language Detection
    │
    ▼
Context Manager
    │
    ├──────────────┐
    ▼              ▼
Translation    Simplification
    │              │
    └──────┬───────┘
           ▼
     LLM Orchestrator
           │
    ┌──────┼─────────────┐
    ▼      ▼             ▼
Reply   Summary      Vocabulary
Coach   Generator    Extractor
    │      │             │
    └──────┴─────────────┘
           ▼
        Overlay UI
```

---

# AI Components

The AI layer consists of independent services.

```
Speech Service

Translation Service

Simplification Service

Context Service

LLM Orchestrator

Reply Coach

Summary Service

Vocabulary Service

Memory Service

RAG Service
```

Each component owns one responsibility.

No service should perform multiple unrelated tasks.

---

# AI Pipeline

Every meeting follows the same processing pipeline.

```
Audio

↓

Voice Detection

↓

Speech Recognition

↓

Transcript Cleaning

↓

Context Building

↓

Translation

↓

German Simplification

↓

AI Analysis

↓

Overlay

↓

Memory

↓

Meeting Review
```

Each stage produces structured output.

Each stage can be tested independently.

---

# Speech Recognition Design

The speech recognition layer converts live audio into timestamped text.

Requirements

- Low latency
- Streaming
- Speaker-aware (future)
- Offline capable
- Configurable models

Preferred providers

1. Faster-Whisper
2. Whisper.cpp
3. OpenAI Whisper API (fallback)

---

# Audio Processing Pipeline

Incoming audio is processed incrementally.

```
Raw Audio

↓

Noise Reduction

↓

Voice Activity Detection

↓

Chunk Creation

↓

Speech Recognition

↓

Transcript Buffer
```

Small chunks reduce latency, but independent local speech-recognition calls also
need enough linguistic context. The current desktop live-transcription profile
uses four-second PCM16 mono chunks with one second of overlap. This deliberately
adds two seconds of audio acquisition before the first decode in exchange for
materially better sentence continuity than the previous two-second profile.
On a normal user-requested capture stop, the desktop flushes the final
uncovered partial interval through the same bounded FIFO before ending the
session; application exit and terminal failure remain bounded abort paths.

Long recordings should never wait for completion before processing.

---

# Voice Activity Detection

Voice Activity Detection (VAD) determines when speech begins and ends.

Responsibilities

- Ignore silence
- Ignore keyboard sounds
- Ignore mouse clicks
- Reduce unnecessary AI calls
- Improve latency

Preferred libraries

- Silero VAD
- WebRTC VAD

The VAD layer should be replaceable through configuration.

---

# Transcript Buffer

The Transcript Buffer stores recent speech.

Responsibilities

- Preserve timestamps
- Preserve speaker IDs (future)
- Merge fragmented sentences
- Remove duplicated transcripts
- Handle streaming updates

The buffer should expose:

- Current transcript
- Previous transcript
- Delta transcript

The rest of the AI pipeline consumes only cleaned transcript data.

---

# Language Detection

The system automatically detects the spoken language.

Supported examples:

- German
- English
- Turkish

Language detection should occur continuously.

Language changes should emit an event.

Example:

SpeechLanguageChanged

---

# Translation Service

The Translation Service translates speech into the user's preferred language.

Responsibilities

- Preserve meaning
- Preserve names
- Preserve technical terminology
- Preserve numbers
- Preserve dates

Translation should never modify intent.

Preferred output:

Structured JSON.

### Live translation scheduling

Live speech-to-text is latency-critical. Translation is opportunistic
best-effort work and must never delay or queue ahead of speech recognition.
For translation-only Assist sessions, the waiting outer Assist work is a
bounded latest-wins mailbox: a newly accepted transcript segment replaces only
an older segment that has not started processing. This prevents stale
translation requests from accumulating during continuous speech. An active
translation remains associated with its own transcript segment and may be
preempted safely when new STT work begins. Sessions that also enable
simplification or reply coaching retain their bounded FIFO ordering semantics.

Example:

```json
{
  "source_language": "de",
  "target_language": "tr",
  "translated_text": "...",
  "confidence": 0.98
}
```

---

# German Simplification

This feature rewrites spoken German into simpler German.

Purpose

Help learners understand native speakers.

Example

Native

> Das müssten wir perspektivisch evaluieren.

Simplified

> Wir sollten das später genauer prüfen.

Rules

- Preserve meaning
- Short sentences
- Common vocabulary
- CEFR-aware (B1/B2)

Simplification is independent from translation.

Users may enable one or both.

---
---

# Context Management

## Purpose

Meetings are continuous conversations.

AI must never process each sentence independently.

Instead, every request should include sufficient conversational context to preserve meaning.

---

## Responsibilities

The Context Manager is responsible for

- maintaining conversation history
- preserving topic continuity
- resolving references
- limiting token usage
- removing irrelevant history

---

## Context Layers

```
Current Sentence
        │
        ▼
Recent Conversation
        │
        ▼
Current Topic
        │
        ▼
Meeting Memory
        │
        ▼
User Preferences
```

Each layer provides additional context.

---

## Context Window

Not every transcript should be sent to the LLM.

Instead, build a dynamic context.

Example

```
Current sentence

+

Last 5 transcript segments

+

Current topic

+

Relevant meeting memory

↓

Prompt
```

---

## Context Size

Recommended defaults

Current transcript

- Always included

Recent history

- Last 5–10 transcript segments

Meeting summary

- Optional

Previous meetings

- Only when relevant

---

## Context Prioritization

Priority order

1. Current transcript
2. Current discussion topic
3. Recent transcript
4. Relevant meeting memory
5. User preferences

Lower-priority context should be discarded first when approaching model limits.

---

# LLM Orchestrator

## Purpose

The LLM Orchestrator is the central coordinator for all AI requests.

No feature should call an LLM provider directly.

```
Feature

↓

LLM Orchestrator

↓

Provider Interface

↓

Selected Provider
```

---

## Responsibilities

- model selection
- prompt execution
- retries
- fallback handling
- token budgeting
- response validation
- telemetry

---

## Request Flow

```
Feature Request

↓

Prompt Builder

↓

Context Manager

↓

Provider Selection

↓

LLM Provider

↓

Response Validation

↓

Structured Result
```

---

# Provider Abstraction

Every provider implements the same interface.

Example

```python
class LLMProvider:

    async def generate(
        self,
        request: PromptRequest
    ) -> PromptResponse:
        ...
```

Supported providers

- OpenAI
- Anthropic
- Gemini
- Ollama
- LM Studio
- Future providers

Business services must never know which provider is active.

---

# Model Selection Strategy

Different tasks require different models.

Example

| Task | Preferred Model |
|------|-----------------|
| Translation | Small Fast Model |
| Simplification | Small Fast Model |
| Reply Coach | Large LLM |
| Summary | Large LLM |
| Vocabulary | Small LLM |

The orchestrator decides automatically.

---

# Prompt Architecture

Prompts are treated as versioned assets.

Never embed prompts inside Python code.

Directory

```
backend/prompts/

translation/

reply_coach/

summary/

grammar/

vocabulary/

learning/
```

Each prompt contains

- metadata
- version
- description
- variables
- expected output

---

# Prompt Template Example

```
System

You are an assistant helping users understand German business meetings.

User

{{transcript}}

Context

{{context}}

Output

Return valid JSON only.
```

Variables are injected by the Prompt Builder.

---

# Prompt Versioning

Every prompt should be version controlled.

Example

```
reply_coach_v1.md

reply_coach_v2.md

reply_coach_v3.md
```

Changing prompts must not require code changes.

---

# Structured Outputs

LLM responses should always be machine-readable.

Preferred format

```json
{
  "reply": "...",
  "confidence": 0.93,
  "keywords": [
    "...",
    "..."
  ]
}
```

Avoid free-form responses whenever possible.

---

# Response Validation

Every LLM response must be validated.

Validation includes

- valid JSON
- required fields
- data types
- confidence range
- length limits

Invalid responses should trigger retry logic.

---

# Reply Coach

## Purpose

Reply Coach helps users formulate their own response.

It should never answer instead of the user.

---

## Responsibilities

- detect questions
- identify intent
- suggest concise replies
- explain difficult wording
- highlight key vocabulary

---

## Example

Meeting

> Können Sie das bis Freitag liefern?

Reply suggestion

> Ja, das sollte bis Freitag möglich sein.

Alternative

> Ich prüfe das und gebe Ihnen heute Nachmittag Bescheid.

---

# Question Detection

Every transcript segment is analyzed.

Possible outputs

```json
{
  "is_question": true,
  "confidence": 0.97,
  "question_type": "deadline"
}
```

Supported categories

- confirmation
- opinion
- deadline
- technical
- planning
- clarification
- decision

---

# Meeting Memory

Meeting Memory stores information relevant only to the active meeting.

Examples

- discussed topics
- participants
- decisions
- unresolved questions
- action items

Memory is cleared after meeting completion unless persisted.

---

# Memory Layers

```
Working Memory

↓

Meeting Memory

↓

Long-Term Memory
```

Working Memory

- seconds

Meeting Memory

- meeting duration

Long-Term Memory

- optional

---

# Memory Updates

Memory should update incrementally.

Avoid rebuilding the entire context after every sentence.

Instead

```
Transcript

↓

Delta

↓

Memory Update

↓

New Context
```

This minimizes latency.

---

# Session State

Every meeting has an isolated session.

A session stores

- transcript
- context
- memory
- vocabulary
- summaries
- AI state

No session should access another session's data.

---

# AI Event Flow

Example

```
TranscriptReceived

↓

LanguageDetected

↓

TranscriptTranslated

↓

SimplifiedGermanGenerated

↓

QuestionDetected

↓

ReplyGenerated

↓

OverlayUpdated
```

Every event should contain structured payloads.

---
---

# Retrieval-Augmented Generation (RAG)

## Purpose

The RAG system enables the assistant to answer questions using trusted knowledge sources instead of relying solely on the LLM.

Primary use cases

- Company terminology
- Internal documentation
- Meeting history
- Personal knowledge base
- Technical documentation

The LLM should generate answers using retrieved context whenever possible.

---

# RAG Architecture

```
User Request
      │
      ▼
Embedding Generator
      │
      ▼
Vector Store
      │
      ▼
Similarity Search
      │
      ▼
Re-ranking
      │
      ▼
Context Builder
      │
      ▼
LLM
      │
      ▼
Structured Response
```

---

# Knowledge Sources

The RAG system may retrieve information from:

- Meeting transcripts
- Company documents
- User notes
- Uploaded PDFs
- Internal wiki
- Technical manuals
- Personal glossary

Each source should be independently configurable.

---

# Embedding Strategy

Embeddings transform text into vector representations for semantic search.

Requirements

- High semantic quality
- Fast generation
- Local execution preferred
- Provider independence

Preferred models

- BAAI/bge-small-en-v1.5
- BAAI/bge-m3
- multilingual-e5-large

Embedding models should be configurable.

---

# Chunking Strategy

Documents should be divided into meaningful chunks.

Guidelines

- Chunk size: 300–600 tokens
- Overlap: 50–100 tokens
- Preserve headings
- Avoid splitting tables
- Keep paragraphs intact when possible

Poor chunking reduces retrieval quality.

---

# Metadata

Every chunk should include metadata.

Example

```json
{
  "document_id": "...",
  "title": "...",
  "page": 4,
  "language": "de",
  "created_at": "...",
  "source": "meeting_notes"
}
```

Metadata enables filtering and traceability.

---

# Retrieval Pipeline

```
Query

↓

Embedding

↓

Vector Search

↓

Top-K Results

↓

Metadata Filtering

↓

Re-ranking

↓

Prompt Context

↓

LLM
```

Each stage should be independently testable.

---

# Re-ranking

Vector similarity alone is not always sufficient.

A re-ranker improves result relevance.

Preferred approaches

- Cross-Encoder models
- BGE Reranker
- Cohere Rerank (optional)

Re-ranking should occur before prompt construction.

---

# Context Builder

The Context Builder assembles retrieved information into a prompt.

Responsibilities

- Remove duplicates
- Sort by relevance
- Respect token limits
- Preserve citations

Only the highest-quality context should be passed to the LLM.

---

# Confidence Scoring

Every AI response should include a confidence score.

Example

```json
{
  "confidence": 0.91
}
```

Confidence may consider

- speech quality
- retrieval score
- model certainty
- validation results

Low-confidence responses should be clearly indicated in the UI.

---

# Hallucination Prevention

The assistant should minimize unsupported answers.

Strategies

- Prefer RAG over model memory
- Require retrieved evidence
- Use structured prompts
- Reject unsupported claims
- Encourage uncertainty when evidence is weak

If the system is unsure, it should say so.

---

# Streaming Strategy

All long-running AI operations should support streaming.

Benefits

- Lower perceived latency
- Faster feedback
- Better user experience

Examples

- Live transcript
- Translation
- Summary generation
- Reply suggestions

---

# Retry Strategy

Transient failures should be retried automatically.

Suggested policy

- Maximum 3 attempts
- Exponential backoff
- Provider-specific handling

Permanent failures should surface meaningful errors.

---

# Fallback Strategy

If a provider fails:

```
Primary Provider

↓

Secondary Provider

↓

Local Model

↓

Graceful Error
```

The user should not experience an application crash because of a single provider failure.

---

# Token Budget Management

LLMs have context limits.

The system should manage tokens intelligently.

Priority

1. Current transcript
2. Relevant context
3. Retrieved documents
4. Meeting memory
5. Older history

Discard low-priority content first.

---

# Cost Optimization

Cloud models may incur usage costs.

Strategies

- Use local models when possible
- Cache repeated requests
- Avoid duplicate prompts
- Select the smallest suitable model
- Batch background tasks where appropriate

Performance and quality should remain acceptable.

---

# AI Evaluation

The system should be evaluated continuously.

Suggested metrics

- Speech Recognition Accuracy
- Translation Quality
- Simplification Quality
- Reply Suggestion Relevance
- Summary Accuracy
- Retrieval Precision
- Retrieval Recall
- Latency
- User Satisfaction

Evaluation datasets should be version controlled.

---

# AI Testing

Every AI module should be testable.

Recommended test categories

- Unit tests
- Integration tests
- Prompt regression tests
- Retrieval tests
- End-to-end scenarios

Whenever prompts change, regression tests should verify that expected outputs remain stable.

---

# Prompt Regression

Prompt changes should not silently reduce quality.

Store representative inputs and expected structured outputs.

Example

```
tests/prompts/

translation/

summary/

reply_coach/

vocabulary/
```

---

# Observability

AI services should expose metrics.

Examples

- request count
- latency
- token usage
- provider usage
- retry count
- failure rate
- cache hit rate

Metrics enable performance tuning and troubleshooting.

---

# Privacy

Meeting data is sensitive.

Requirements

- Encrypt persisted data
- Never log raw meeting audio
- Mask secrets
- Minimize retained personal data
- Allow user-controlled deletion

Privacy requirements apply to all providers.

---

# Security

Protect against

- prompt injection
- malicious documents
- oversized inputs
- unsupported file types
- denial-of-service scenarios

Validate all external inputs before processing.

---

# Future AI Extensions

The architecture should support future capabilities without major refactoring.

Examples

- Multi-speaker diarization
- Emotion detection (optional)
- Presentation coaching
- Pronunciation feedback
- Calendar integration
- Email draft generation
- Company-specific assistants
- Knowledge graph integration
- Offline AI bundle
- On-device fine-tuned models

These should integrate through existing interfaces and events.

---

# AI Design Summary

The AI system is designed to be

- modular
- provider agnostic
- privacy first
- local first
- event driven
- testable
- observable
- extensible

Each AI capability is implemented as an independent service.

The architecture prioritizes maintainability, correctness, and user trust over unnecessary complexity.

The assistant exists to help users communicate more confidently—not to replace their participation in meetings.

---
