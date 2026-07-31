# ADR-002: Use FastAPI as the Backend Framework

## Status

Accepted

## Date

2026-07-22

---

# Context

The backend coordinates

- AI orchestration
- meeting sessions
- speech processing
- translation
- memory
- RAG
- API endpoints
- WebSocket streaming

Requirements

- async support
- type safety
- OpenAPI generation
- high performance
- dependency injection
- modern Python ecosystem

---

# Decision

Use **FastAPI** as the primary backend framework.

---

# Alternatives Considered

## Flask

Pros

- simple
- mature

Cons

- synchronous by default
- additional libraries required
- weaker typing

Rejected.

---

## Django

Pros

- batteries included

Cons

- unnecessary complexity
- ORM tightly coupled
- heavier framework

Rejected.

---

## Litestar

Pros

- modern

Cons

- smaller ecosystem
- fewer production examples

Rejected.

---

# Rationale

FastAPI provides

- async-first architecture
- automatic validation
- OpenAPI generation
- excellent typing support
- strong community adoption

---

# Consequences

Positive

- high throughput
- low latency
- automatic documentation
- clean dependency injection

Negative

- async learning curve
- dependency management requires discipline

---

# Guidelines

Business logic

↓

Services

Controllers

↓

Validation only

Repositories

↓

Persistence only

---

# Related Documents

- API Design
- System Architecture
- Coding Standards

---

# Decision

Accepted