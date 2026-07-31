# AI Meeting Copilot

# Database Design

Version: 1.0

Status: Active

---

# Purpose

This document defines the database architecture, data model, relationships, persistence strategy, and repository rules for AI Meeting Copilot.

The database should be

- modular
- normalized
- extensible
- migration-friendly
- provider independent

Business logic must never exist inside the database.

---

# Database Principles

The database is responsible for persistence only.

The application layer owns business logic.

Repositories own data access.

Services never execute SQL directly.

---

# Database Engine

Current

SQLite

Reasons

- local desktop application
- zero configuration
- reliable
- lightweight

Future

PostgreSQL

The repository layer should allow switching database engines without changing business logic.

---

# ORM

Preferred

SQLAlchemy 2.x

Migration Tool

Alembic

Never write application logic inside ORM models.

---

# Database Architecture

```
Application

↓

Repository

↓

SQLAlchemy

↓

SQLite

↓

Disk
```

Only repositories communicate with SQLAlchemy.

---

# Entity Relationship Overview

```
Meeting
   │
   ├──────── Transcript
   │
   ├──────── Summary
   │
   ├──────── Vocabulary
   │
   ├──────── ActionItem
   │
   └──────── AIEvent

Settings

UserPreference

KnowledgeDocument

DocumentChunk
```

---

# Naming Convention

Tables

snake_case

Examples

meetings

transcripts

meeting_summary

action_items

Columns

snake_case

Primary Key

id

Foreign Keys

meeting_id

document_id

created_at

updated_at

---

# Common Columns

Most tables should contain

```
id

created_at

updated_at
```

Optional

deleted_at

for future soft delete support.

---

# Table: meetings

Purpose

Represents one meeting session.

Columns

```
id

title

language

status

started_at

ended_at

created_at

updated_at
```

Status

```
ACTIVE

COMPLETED

CANCELLED
```

---

# Table: transcripts

Stores recognized speech.

Columns

```
id

meeting_id

speaker

sequence

original_text

translated_text

simplified_text

confidence

started_at

ended_at

created_at
```

Relationships

Meeting

↓

Many Transcripts

---

# Table: summaries

Stores AI-generated meeting summaries.

Columns

```
id

meeting_id

summary

version

generated_at
```

Multiple summary versions may exist.

Only the newest is active.

---

# Table: action_items

Stores extracted tasks.

Columns

```
id

meeting_id

description

owner

deadline

completed

created_at
```

---

# Table: vocabulary

Stores vocabulary discovered during meetings.

Columns

```
id

meeting_id

term

translation

language

level

example

created_at
```

Future

Spaced repetition metadata may be added.

---

# Table: ai_events

Stores important AI events.

Examples

Translation Generated

Summary Updated

Provider Changed

Columns

```
id

meeting_id

event_type

payload

created_at
```

Payload should be JSON.

---

# Table: settings

Application settings.

Columns

```
id

preferred_language

translation_enabled

simplification_enabled

overlay_position

provider

theme

created_at
```

Normally only one row exists.

---

# Table: user_preferences

Stores learning preferences.

Examples

Preferred CEFR level

Preferred translation language

Learning mode

Columns

```
id

target_language

german_level

learning_mode

created_at
```

---

# Table: knowledge_documents

Documents available for RAG.

Columns

```
id

title

source

language

checksum

created_at
```

Checksum prevents duplicate ingestion.

---

# Table: document_chunks

Stores document chunks.

Columns

```
id

document_id

chunk_index

text

embedding_id

metadata

created_at
```

Metadata stored as JSON.

---

# Relationships

```
Meeting

↓

Transcript

↓

Vocabulary

↓

Summary

↓

Action Items

↓

AI Events
```

Documents remain independent.

---

# Repository Pattern

Each aggregate has one repository.

Examples

MeetingRepository

TranscriptRepository

SummaryRepository

VocabularyRepository

DocumentRepository

Repositories own persistence.

Services own business logic.

---

# Repository Interface Example

```python
class MeetingRepository:

    async def create(...)

    async def get(...)

    async def update(...)

    async def delete(...)
```

Repositories should expose only business-relevant operations.

---

# Data Validation

Validation occurs before persistence.

Examples

- UUID format
- required fields
- language codes
- confidence range
- timestamps

Invalid data should never reach the database.

---

---

# Constraints

Every table should define appropriate constraints.

Examples

Primary Key

```
PRIMARY KEY (id)
```

Foreign Key

```
FOREIGN KEY (meeting_id)
REFERENCES meetings(id)
```

Unique

```
checksum

email (future)

provider_name
```

Check Constraints

Examples

```
confidence >= 0

confidence <= 1
```

```
sequence >= 0
```

Database constraints should protect data integrity even if application validation fails.

---

# Foreign Keys

Relationships should be enforced.

Example

Meeting

↓

Transcript

↓

Vocabulary

↓

Action Items

Deleting a meeting should never leave orphaned rows.

Preferred strategy

Application-controlled deletion.

Avoid database cascade deletes unless explicitly required.

---

# Index Strategy

Indexes should optimize common queries.

Meeting Table

Indexes

```
started_at

status
```

Transcript Table

Indexes

```
meeting_id

sequence

speaker

created_at
```

Vocabulary

Indexes

```
meeting_id

term
```

Documents

Indexes

```
checksum

language
```

Do not create indexes for columns that are rarely queried.

---

# Composite Indexes

Examples

```
(meeting_id, sequence)
```

```
(meeting_id, created_at)
```

Composite indexes should match real query patterns.

---

# Transactions

Every write operation should execute inside a transaction.

Example

```
Create Meeting

↓

Insert Meeting

↓

Insert Initial Settings

↓

Commit
```

If any step fails

↓

Rollback

Repositories should never leave the database in a partial state.

---

# Transaction Rules

Transactions should be

- short
- atomic
- isolated
- deterministic

Never perform long AI inference while holding an open database transaction.

---

# Migration Strategy

Schema changes should use Alembic.

Never modify production databases manually.

Migration example

```
alembic revision --autogenerate

↓

Review

↓

Apply

↓

Commit
```

Every migration must be reviewed before merging.

---

# Migration Rules

Each migration should

- perform one logical change
- be reversible where possible
- include descriptive names

Example

```
add_vocabulary_table

add_summary_version

rename_provider_column
```

---

# Repository Rules

Repositories are responsible only for persistence.

Allowed

- insert
- update
- delete
- query

Forbidden

- prompt generation
- translation
- business decisions
- HTTP requests

Repositories should remain framework-independent where practical.

---

# Soft Delete

Soft delete is optional for future versions.

Pattern

```
deleted_at
```

Records marked as deleted should be excluded from normal queries.

Permanent deletion should be explicit.

---

# Audit Strategy

Important actions may be recorded.

Examples

- Meeting deleted
- Settings changed
- Provider changed
- Document imported

Audit records should include

- timestamp
- action
- entity
- actor (future)

---

# Full Text Search

Future capability

Meeting transcripts should support full-text search.

SQLite

FTS5

PostgreSQL

tsvector

Search implementation should remain behind repository interfaces.

---

# Vector Store Integration

Embeddings should not be stored directly inside business tables.

Recommended architecture

```
Knowledge Document

↓

Chunk

↓

Embedding

↓

Vector Store
```

Default

FAISS

Future

pgvector

Qdrant

Milvus

The application should access vectors through a VectorRepository abstraction.

---

# Caching

Frequently accessed data may be cached.

Examples

- Settings
- User Preferences
- Provider Configuration

Meeting transcripts should not be aggressively cached because they change continuously.

---

# Connection Management

Only one database engine should exist per application instance.

Repositories receive sessions through dependency injection.

Avoid creating ad-hoc database connections.

---

# Performance Guidelines

Optimize for

- fast transcript inserts
- efficient meeting retrieval
- responsive UI
- predictable query times

Avoid

- N+1 queries
- unnecessary joins
- loading entire transcript history when only recent segments are required

---

# Database Testing

Every repository should have automated tests.

Recommended categories

- CRUD operations
- transaction rollback
- constraints
- migrations
- query performance

Tests should use isolated databases.

---

# Backup Strategy

Meeting data should be exportable.

Supported formats

- SQLite backup
- JSON export
- Markdown export
- PDF export (future)

Automatic backup should be configurable.

---

# Recovery

Database recovery should support

- startup validation
- corruption detection
- backup restore

If recovery fails, the application should notify the user without crashing.

---

# Security

Protect stored data.

Recommendations

- encrypt sensitive exports
- restrict file permissions
- validate imported documents
- avoid storing secrets in the database

API keys belong in environment variables or secure storage.

---

# Privacy

Meeting content is sensitive.

Requirements

- local storage by default
- user-controlled deletion
- configurable retention
- no hidden uploads

Cloud synchronization should always require explicit user consent.

---

# Future Scalability

The database design should support future capabilities.

Examples

- Multiple users
- Shared workspaces
- Cloud synchronization
- Calendar integration
- Team knowledge bases
- Enterprise deployments

The schema should evolve through migrations rather than manual edits.

---

# Database Design Summary

The persistence layer follows these principles

- Repository Pattern
- Clean Architecture
- Provider Independence
- Migration-first
- Strong Constraints
- Transaction Safety
- Performance Awareness
- Privacy by Design

The database stores state.

Business logic remains in the application layer.

Repositories provide the only interface between the application and persistent storage.

---
