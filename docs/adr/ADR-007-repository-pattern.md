# ADR-007: Adopt the Repository Pattern

## Status

Accepted

## Date

2026-07-22

## Decision Makers

Project Maintainers

---

# Context

The application persists multiple types of data.

Examples

- meetings
- transcripts
- summaries
- vocabulary
- settings
- documents
- embeddings
- action items

Without a clear persistence abstraction, SQL queries would spread across the application, making maintenance, testing, and future database migrations difficult.

---

# Decision

All database access must go through repositories.

Business services must never execute SQL directly.

---

# Architecture

```
Presentation Layer
        │
        ▼
Application Services
        │
        ▼
Repositories
        │
        ▼
SQLAlchemy
        │
        ▼
SQLite / PostgreSQL
```

---

# Responsibilities

Repositories are responsible for

- CRUD operations
- query construction
- transaction boundaries (when applicable)
- mapping database models to domain objects

Repositories are **not** responsible for

- business rules
- AI inference
- prompt generation
- HTTP requests
- event publishing

---

# Repository Examples

```
MeetingRepository

TranscriptRepository

SummaryRepository

VocabularyRepository

SettingsRepository

DocumentRepository

EmbeddingRepository

ProviderRepository
```

---

# Benefits

- Separation of concerns
- Easier testing
- Database independence
- Cleaner service layer
- Consistent data access
- Improved maintainability

---

# Alternatives Considered

## SQL Inside Services

Advantages

- Fewer files

Disadvantages

- Tight coupling
- Difficult testing
- Duplicate queries
- Hard migration path

Rejected.

---

# Risks

Repositories can become too large.

Mitigation

Keep repositories focused on a single aggregate or bounded context.

---

# Implementation Guidelines

- One repository per aggregate root.
- Keep methods small and intention-revealing.
- Prefer expressive method names (e.g., `find_active_meeting()` over generic queries).
- Avoid exposing ORM internals to the service layer.

---

# Related Documents

- 03-project-structure.md
- 04-coding-standards.md
- 08-database-design.md

---

# Decision

Accepted