# ADR-005: Introduce a Provider Abstraction Layer

## Status

Accepted

## Date

2026-07-22

---

# Context

The application relies on multiple AI providers.

Examples

- OpenAI
- Anthropic
- Google Gemini
- Ollama
- LM Studio
- Azure OpenAI
- AWS Bedrock

Each provider exposes different

- APIs
- authentication
- parameters
- streaming implementations
- token accounting
- rate limits

Embedding provider-specific logic throughout the codebase would make maintenance difficult.

---

# Decision

All LLM providers must be accessed through a common Provider Interface.

Business logic must never communicate with providers directly.

---

# Architecture

```
Application

↓

AI Service

↓

Provider Interface

↓

OpenAI Provider

Anthropic Provider

Gemini Provider

Ollama Provider

LM Studio Provider
```

---

# Responsibilities

Provider Interface

- send prompt
- stream response
- generate embeddings
- estimate tokens
- normalize errors
- expose model metadata

---

# Provider Responsibilities

Each provider implements

- authentication
- retries
- streaming
- provider-specific API calls
- response normalization

---

# Forbidden

Business logic must never contain

```
openai.ChatCompletion(...)

anthropic.messages.create(...)

google.generativeai(...)
```

These calls belong only inside provider implementations.

---

# Benefits

- Provider independence
- Easier testing
- Simple provider switching
- Cleaner architecture
- Reduced vendor lock-in

---

# Alternatives Considered

## Direct SDK Usage

Advantages

- Fast development

Disadvantages

- Vendor lock-in
- Code duplication
- Difficult migration

Rejected.

---

# Risks

The abstraction layer introduces additional code.

Mitigation

Keep the interface minimal and focused.

---

# Future Compatibility

The abstraction should support

- local models
- cloud models
- hybrid deployments
- enterprise gateways

without modifying business logic.

---

# Related Documents

- AI Design
- API Design
- Coding Standards

---

# Decision

Accepted