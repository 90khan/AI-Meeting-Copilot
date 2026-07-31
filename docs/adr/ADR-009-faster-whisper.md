# ADR-009: Use Faster-Whisper as the Default Speech Recognition Engine

## Status

Accepted

## Date

2026-07-22

---

# Context

Speech recognition is a core capability of AI Meeting Copilot.

The engine must support

- real-time transcription
- multilingual speech
- offline operation
- high accuracy
- efficient resource usage

---

# Decision

Use Faster-Whisper as the default speech recognition engine.

The implementation should allow replacing it through the Speech Recognition Interface.

---

# Alternatives Considered

## OpenAI Whisper API

Advantages

- High accuracy
- Managed infrastructure

Disadvantages

- Internet dependency
- API costs
- Privacy concerns

Rejected as the default.

---

## Vosk

Advantages

- Offline
- Lightweight

Disadvantages

- Lower transcription quality for multilingual meetings

Rejected.

---

## Deepgram

Advantages

- Excellent streaming

Disadvantages

- Cloud-only
- Vendor dependency

Rejected as the default.

---

# Rationale

Faster-Whisper provides

- high transcription quality
- GPU acceleration
- CPU support
- offline execution
- low latency
- active community

---

# Benefits

- Privacy-first
- Offline capability
- Reduced operational cost
- Flexible deployment

---

# Risks

Large models require significant hardware resources.

Mitigation

Support multiple model sizes (tiny, base, small, medium, large) and allow user configuration.

---

# Future

The speech recognition interface should support additional engines without changing application services.

---

# Related Documents

- 05-ai-design.md
- 02-system-architecture.md

---

# Decision

Accepted