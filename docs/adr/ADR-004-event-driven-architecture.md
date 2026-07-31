# ADR-004: Adopt an Event-Driven Architecture

## Status

Accepted

## Date

2026-07-22

## Decision Makers

Project Maintainers

---

# Context

AI Meeting Copilot performs many independent tasks simultaneously:

- Speech recognition
- Voice activity detection (VAD)
- Real-time translation
- German simplification
- Meeting summarization
- Vocabulary extraction
- Reply coaching
- Overlay updates
- Logging
- Metrics collection
- Persistence

These operations occur continuously and at different speeds.

A tightly coupled architecture would create cascading dependencies, reduce maintainability, and make it difficult to introduce new AI features.

---

# Decision

The application will adopt an **event-driven architecture**.

Independent modules communicate by publishing and subscribing to events instead of directly invoking each other.

---

# Event Flow

```
Audio Input
      │
      ▼
Speech Recognition
      │
      ▼
TranscriptCreated
      │
 ┌────┼──────────────┐
 ▼    ▼              ▼
Translation     Persistence
 ▼                  ▼
German         Database
Simplification
 ▼
Overlay
 ▼
Reply Coach
 ▼
Summary Engine
```

---

# Event Bus Responsibilities

The Event Bus is responsible for

- publishing events
- subscribing handlers
- dispatching asynchronously
- isolating components
- avoiding circular dependencies

It must not contain business logic.

---

# Event Naming Convention

Events should use the past tense.

Examples

```
TranscriptCreated

TranslationGenerated

SummaryUpdated

MeetingStarted

MeetingEnded

ProviderChanged

VocabularyExtracted

DocumentIndexed

EmbeddingCreated

SettingsUpdated
```

---

# Event Payload

Every event should contain

```
event_id

event_type

timestamp

meeting_id

payload
```

Payload should remain small and serializable.

---

# Benefits

- Loose coupling
- Independent modules
- Easier testing
- Better extensibility
- Easier debugging
- Parallel processing
- Future distributed architecture support

---

# Alternatives Considered

## Direct Service Calls

Advantages

- Simple

Disadvantages

- Tight coupling
- Circular dependencies
- Difficult testing
- Hard to extend

Rejected.

---

## Shared Global State

Advantages

- Easy implementation

Disadvantages

- Race conditions
- Hidden dependencies
- Difficult debugging

Rejected.

---

# Risks

Excessive event creation may reduce readability.

Mitigation

- Use clear event names
- Document all events
- Keep payloads minimal
- Avoid event chains longer than necessary

---

# Implementation Guidelines

Events should be immutable.

Subscribers must not modify event objects.

Handlers should be idempotent whenever possible.

Long-running AI inference should execute asynchronously.

---

# Related Documents

- 02-system-architecture.md
- 05-ai-design.md
- 06-development-guide.md

---

# Decision

Accepted