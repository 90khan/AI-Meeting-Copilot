# ADR-006: Adopt a Local-First AI Strategy

## Status

Accepted

## Date

2026-07-22

---

# Context

AI Meeting Copilot processes highly sensitive information.

Examples

- customer meetings
- business discussions
- legal conversations
- financial information
- internal documentation

Many organizations prohibit uploading confidential data to third-party cloud services.

Privacy is therefore a core project requirement.

---

# Decision

The application adopts a **Local-First AI** strategy.

Whenever practical, processing should occur on the user's device before cloud services are considered.

Cloud providers remain optional.

---

# Processing Priority

Preferred order

```
Local Processing

↓

Local Models

↓

Local Vector Store

↓

Cloud AI (Optional)
```

---

# Local Components

Examples

- Faster-Whisper
- Silero VAD
- SQLite
- FAISS
- Ollama
- LM Studio

---

# Cloud Components

Optional

- OpenAI
- Anthropic
- Gemini
- Azure OpenAI
- AWS Bedrock

Cloud services require explicit user configuration.

---

# Privacy Principles

Meeting data

- stays local by default
- is never uploaded automatically
- remains user controlled
- can be permanently deleted

---

# Benefits

- Better privacy
- Offline capability
- Lower cloud costs
- Reduced latency
- Regulatory compliance
- Enterprise adoption

---

# Risks

Local AI models may require significant CPU, GPU, or memory resources.

Mitigation

Allow users to configure processing modes based on available hardware.

---

# Alternatives Considered

## Cloud-First

Advantages

- Stronger models
- Less local computation

Disadvantages

- Privacy concerns
- Network dependency
- Ongoing API costs

Rejected.

---

# Future

Support hybrid deployments where

- transcription is local
- embeddings are local
- summarization can optionally use cloud models

depending on organizational policies.

---

# Related Documents

- Project Principles
- AI Design
- Database Design

---

# Decision

Accepted