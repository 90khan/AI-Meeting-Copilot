# AI Meeting Copilot

# API Design

Version: 1.0

Status: Active

---

# Purpose

This document defines the public API contracts for AI Meeting Copilot.

The objectives are

- consistency
- simplicity
- strong typing
- versioning
- maintainability
- provider independence

All frontend applications communicate only through these APIs.

Business logic must never exist inside API endpoints.

---

# API Architecture

```
React UI

↓

REST API

↓

Application Services

↓

Domain Services

↓

Repositories

↓

Database
```

Streaming APIs

```
Speech

↓

WebSocket

↓

Frontend
```

---

# API Principles

Every API should be

- predictable
- typed
- versioned
- documented
- testable
- secure

APIs should expose business capabilities rather than implementation details.

---

# API Style

Architecture

REST

Streaming

WebSocket

Data Format

JSON

Encoding

UTF-8

Time Format

ISO-8601

Timezone

UTC

---

# Base URL

Development

```
http://localhost:8000/api/v1
```

Production

```
https://api.company.com/api/v1
```

Every endpoint is versioned.

---

# API Versioning

Pattern

```
/api/v1/
```

Examples

```
/api/v1/meetings

/api/v1/settings

/api/v1/transcripts
```

Breaking changes require a new API version.

---

# Authentication

Current Version

Desktop Application

Local User

No login required.

Future

OAuth2

OpenID Connect

Azure AD

Google

Enterprise SSO

Authentication should be implemented as middleware.

---

# Authorization

Future role examples

```
Admin

User

Viewer
```

Authorization belongs to the application layer.

Never inside controllers.

---

# Standard Response Format

Success

```json
{
  "success": true,
  "data": {},
  "metadata": {}
}
```

Failure

```json
{
  "success": false,
  "error": {
    "code": "TRANSCRIPT_NOT_FOUND",
    "message": "Transcript not found."
  }
}
```

Every endpoint should follow the same response format.

---

# Pagination

Collection endpoints support pagination.

Example

```
GET /meetings?page=1&page_size=20
```

Response

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 120
}
```

---

# Filtering

Example

```
GET /meetings?language=de
```

```
GET /meetings?date=2026-07-20
```

```
GET /meetings?status=completed
```

Filters should be combinable.

---

# Sorting

Example

```
GET /meetings?sort=start_time
```

Descending

```
GET /meetings?sort=-start_time
```

---

# Resource Naming

Good

```
/meetings

/transcripts

/settings

/vocabulary
```

Avoid

```
/getMeeting

/createMeeting

/deleteMeeting
```

Use HTTP verbs instead.

---

# HTTP Methods

GET

Read

POST

Create

PUT

Replace

PATCH

Partial Update

DELETE

Delete

---

# Status Codes

200 OK

201 Created

204 No Content

400 Bad Request

401 Unauthorized

403 Forbidden

404 Not Found

409 Conflict

422 Validation Error

500 Internal Error

Use standard HTTP status codes consistently.

---

# Validation

Every request should be validated.

Validation occurs before reaching business logic.

Examples

- required fields
- enums
- string lengths
- numeric ranges
- UUID format

---

# Request DTOs

Controllers receive DTOs only.

Example

```python
class StartMeetingRequest(BaseModel):

    language: str

    translation_enabled: bool

    simplification_enabled: bool
```

Never expose database models directly.

---

# Response DTOs

Example

```python
class MeetingResponse(BaseModel):

    id: UUID

    title: str

    language: str

    started_at: datetime
```

---

# Error Model

```json
{
  "success": false,
  "error": {
    "code": "...",
    "message": "...",
    "details": {}
  }
}
```

Internal stack traces must never be returned.

---

# Health Endpoints

```
GET /health
```

Response

```json
{
  "status":"healthy"
}
```

Detailed endpoint

```
GET /health/details
```

Returns

- database
- speech service
- provider
- vector store
- memory

---

# Metrics Endpoint

```
GET /metrics
```

Available only in development or protected environments.

Supports Prometheus-compatible metrics.

---

# Configuration Endpoints

```
GET /settings
```

```
PATCH /settings
```

Settings examples

- preferred language
- overlay position
- translation mode
- simplification level
- AI provider

---

# Meeting Endpoints

Create Meeting

```
POST /meetings
```

List Meetings

```
GET /meetings
```

Meeting Details

```
GET /meetings/{id}
```

Stop Meeting

```
POST /meetings/{id}/stop
```

Delete Meeting

```
DELETE /meetings/{id}
```

---

# Transcript Endpoints

Get Transcript

```
GET /transcripts/{meeting_id}
```

Export Transcript

```
GET /transcripts/{meeting_id}/export
```

Supported formats

- TXT
- Markdown
- PDF (future)

---
---

# WebSocket API

## Purpose

The WebSocket connection provides low-latency streaming updates during active meetings.

Unlike REST endpoints, WebSocket messages are event-driven.

---

## Endpoint

```
GET /ws
```

A single WebSocket connection should support all real-time events.

---

## Connection Lifecycle

```
Client Connects

↓

Handshake

↓

Authentication (future)

↓

Subscribe

↓

Streaming Events

↓

Disconnect
```

The server should gracefully handle reconnections.

---

# Message Envelope

Every WebSocket message follows the same structure.

```json
{
  "event": "TranscriptUpdated",
  "timestamp": "2026-07-22T12:30:15Z",
  "payload": {}
}
```

This envelope ensures consistent parsing across all clients.

---

# Event Types

Supported events

- MeetingStarted
- MeetingStopped
- TranscriptUpdated
- TranscriptCorrected
- LanguageDetected
- TranslationUpdated
- SimplifiedGermanUpdated
- ReplySuggestionUpdated
- VocabularyExtracted
- SummaryUpdated
- ActionItemsUpdated
- ProviderChanged
- Warning
- Error

New events should follow the same naming convention.

---

# TranscriptUpdated

Payload example

```json
{
  "event": "TranscriptUpdated",
  "payload": {
    "meeting_id": "uuid",
    "speaker": "Speaker A",
    "text": "Wir beginnen jetzt.",
    "timestamp": "2026-07-22T12:31:05Z"
  }
}
```

---

# TranslationUpdated

```json
{
  "event": "TranslationUpdated",
  "payload": {
    "language": "tr",
    "text": "Şimdi başlıyoruz."
  }
}
```

---

# ReplySuggestionUpdated

```json
{
  "event": "ReplySuggestionUpdated",
  "payload": {
    "reply": "Ja, das ist möglich.",
    "confidence": 0.94
  }
}
```

---

# SummaryUpdated

```json
{
  "event": "SummaryUpdated",
  "payload": {
    "summary": "...",
    "version": 2
  }
}
```

Summaries may be updated incrementally during long meetings.

---

# AI Endpoints

The AI layer is exposed through dedicated endpoints.

Examples

```
POST /ai/translate

POST /ai/simplify

POST /ai/reply

POST /ai/summarize

POST /ai/vocabulary
```

Controllers delegate all work to application services.

---

# Translation API

Request

```json
{
  "text": "Wir sollten das später prüfen.",
  "target_language": "tr"
}
```

Response

```json
{
  "success": true,
  "data": {
    "translated_text": "Bunu daha sonra incelemeliyiz.",
    "confidence": 0.99
  }
}
```

---

# Simplification API

Request

```json
{
  "text": "Das müssten wir perspektivisch evaluieren."
}
```

Response

```json
{
  "success": true,
  "data": {
    "simplified_text": "Wir sollten das später genauer prüfen."
  }
}
```

---

# Reply Coach API

Request

```json
{
  "transcript": "Können Sie das bis Freitag liefern?",
  "context": {}
}
```

Response

```json
{
  "success": true,
  "data": {
    "reply": "Ja, das sollte bis Freitag möglich sein.",
    "confidence": 0.93,
    "keywords": [
      "Freitag",
      "Lieferung"
    ]
  }
}
```

---

# Vocabulary API

Response example

```json
{
  "success": true,
  "data": {
    "words": [
      {
        "term": "evaluieren",
        "translation": "değerlendirmek",
        "level": "B2"
      }
    ]
  }
}
```

Vocabulary entries should include language-learning metadata where available.

---

# Meeting Summary API

```
GET /meetings/{id}/summary
```

Response

```json
{
  "summary": "...",
  "action_items": [],
  "decisions": [],
  "questions": []
}
```

---

# Export API

Supported endpoints

```
GET /meetings/{id}/export/txt

GET /meetings/{id}/export/md

GET /meetings/{id}/export/pdf

GET /meetings/{id}/export/json
```

Exports should be generated asynchronously if processing is expensive.

---

# Idempotency

Operations that may be retried safely should support idempotency keys.

Example

```
Idempotency-Key:
8e57a9b0-...
```

Repeated requests with the same key should not create duplicate resources.

---

# Rate Limiting

Although the desktop application is primarily local, future cloud APIs should support rate limiting.

Suggested headers

```
X-RateLimit-Limit

X-RateLimit-Remaining

Retry-After
```

---

# API Security

All APIs should validate

- request size
- content type
- JSON schema
- authentication (future)
- authorization (future)

Reject malformed requests before reaching business logic.

---

# API Documentation

Every endpoint should be documented using OpenAPI.

Requirements

- request schema
- response schema
- examples
- status codes
- error responses

Interactive documentation should be available during development.

---

# API Compatibility

Avoid breaking existing clients.

Guidelines

- Add optional fields before removing existing ones.
- Deprecate endpoints before removal.
- Version breaking changes.

---

# Error Codes

Examples

- INVALID_REQUEST
- VALIDATION_ERROR
- MEETING_NOT_FOUND
- TRANSCRIPT_NOT_FOUND
- PROVIDER_UNAVAILABLE
- MODEL_TIMEOUT
- MEMORY_ERROR
- EXPORT_FAILED
- INTERNAL_ERROR

Codes should remain stable across releases.

---

# API Testing

Every endpoint should include

- happy path tests
- validation tests
- authorization tests (future)
- error handling tests
- performance tests where appropriate

WebSocket events should also be covered by automated tests.

---

# API Evolution Strategy

When introducing new functionality

- Prefer additive changes.
- Maintain backward compatibility.
- Document deprecations.
- Keep DTOs versioned if necessary.

Avoid introducing breaking changes without a new API version.

---

# API Design Summary

The API is designed to be

- RESTful
- event-driven
- strongly typed
- versioned
- secure
- testable
- extensible

Controllers remain thin.

Business logic lives inside services.

The API serves as a stable contract between the frontend, backend, and future integrations.

---