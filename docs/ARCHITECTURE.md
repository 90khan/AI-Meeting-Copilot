# AI Meeting Copilot

# Architecture Overview

Version: 1.0

Status: Active

---

# Purpose

This document provides a high-level overview of the AI Meeting Copilot architecture.

Unlike the detailed architecture specification (`02-system-architecture.md`), this document focuses on helping developers quickly understand how the entire system works and how its major components interact.

This should be the first technical document read after the project overview.

---

# Architectural Goals

The system is designed to be

* Modular
* Event-driven
* Privacy-first
* Local-first
* Provider-agnostic
* Testable
* Scalable
* Maintainable

Every architectural decision should support these goals.

---

# High-Level Architecture

```
                 +----------------------+
                 |   Desktop (Tauri)    |
                 +----------+-----------+
                            |
                            |
                 +----------v-----------+
                 |     React Frontend   |
                 +----------+-----------+
                            |
                    WebSocket / REST
                            |
                 +----------v-----------+
                 |    FastAPI Backend   |
                 +----------+-----------+
                            |
          +-----------------+------------------+
          |                 |                  |
          |                 |                  |
   AI Services        Repository Layer    Event Bus
          |                 |                  |
          |                 |                  |
     Provider API      SQLAlchemy        Subscribers
          |                 |
          |                 |
      LLM Providers      SQLite
```

---

# Technology Stack

## Desktop

* Tauri

## Frontend

* React
* TypeScript
* Vite

## Backend

* FastAPI
* Python 3.11+

## AI

* Faster-Whisper
* Silero VAD
* Sentence Transformers
* FAISS

## Database

* SQLite
* SQLAlchemy
* Alembic

## Infrastructure

* Docker
* Docker Compose

---

# System Layers

The application is organized into distinct layers.

```
Presentation

↓

Application

↓

AI Services

↓

Repositories

↓

Persistence
```

Each layer has a single responsibility.

Communication should occur only between neighboring layers.

---

# Desktop Layer

Responsibilities

* Window management
* System tray
* Notifications
* Global shortcuts
* Overlay window
* Native OS integrations

The desktop layer should not contain business logic.

---

# Frontend Layer

Responsibilities

* User interface
* State management
* User interactions
* WebSocket communication
* REST communication
* Rendering AI output

The frontend should remain thin and delegate business logic to the backend.

---

# Backend Layer

The backend coordinates all business operations.

Responsibilities include

* Meeting lifecycle
* AI orchestration
* Event handling
* Repository coordination
* Provider selection
* Configuration
* Authentication (future)
* Logging

---

# Repository Layer

Repositories isolate persistence from business logic.

Responsibilities

* CRUD operations
* Query execution
* Transactions
* Entity mapping

Repositories never contain business rules.

---

# Persistence Layer

The persistence layer stores

* Meetings
* Transcripts
* Summaries
* Vocabulary
* Documents
* Settings
* AI Events

SQLite is the default storage engine.

PostgreSQL support is planned for enterprise deployments.

---

# AI Layer

The AI layer provides intelligent capabilities.

Modules include

* Speech Recognition
* Translation
* German Simplification
* Reply Coach
* Summary Generation
* Vocabulary Extraction
* RAG
* Embedding Service
* Context Builder

Each module should be independently replaceable.

---

# Provider Layer

All external AI providers are accessed through a common Provider Interface.

Supported providers may include

* OpenAI
* Anthropic
* Google Gemini
* Ollama
* LM Studio
* Azure OpenAI
* AWS Bedrock

The application layer never communicates directly with provider SDKs.

---

# Event Bus

The Event Bus enables loose coupling.

Example flow

```
TranscriptCreated

↓

TranslationGenerated

↓

OverlayUpdated

↓

SummaryUpdated

↓

VocabularyExtracted
```

Modules communicate through events rather than direct dependencies whenever practical.

---

# Meeting Lifecycle

Typical meeting flow

```
Meeting Created

↓

Audio Capture

↓

Speech Recognition

↓

Transcript Generated

↓

Translation

↓

Simplification

↓

Overlay Update

↓

Summary

↓

Persistence

↓

Meeting Completed
```

Each stage emits events that downstream modules consume.

---

# Design Principles

The architecture emphasizes

* Single Responsibility
* Dependency Injection
* Explicit Interfaces
* Composition over Inheritance
* Immutable Events
* Separation of Concerns

These principles guide all implementation decisions.

---

# Continue in Part 2

The next section covers

* AI Pipeline
* RAG Architecture
* Data Flow
* Request Lifecycle
* Security Architecture
* Deployment
* Scalability
* Observability
* Error Handling
* Future Evolution
# AI Pipeline

The AI pipeline transforms raw meeting audio into actionable intelligence.

High-level flow

```text
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
Translation
      │
      ▼
German Simplification
      │
      ▼
Context Builder
      │
      ▼
Reply Coach
      │
      ▼
Summary Generator
      │
      ▼
Vocabulary Extractor
      │
      ▼
Persistence
```

Every stage should operate independently and communicate through events where appropriate.

---

# RAG Architecture

Retrieval-Augmented Generation (RAG) enriches AI responses with project-specific knowledge.

Pipeline

```text
Documents
      │
      ▼
Chunking
      │
      ▼
Embedding Generation
      │
      ▼
Vector Store (FAISS)
      │
      ▼
Retriever
      │
      ▼
Re-ranker (optional)
      │
      ▼
Context Builder
      │
      ▼
LLM
```

Responsibilities

* retrieve relevant information
* reduce hallucinations
* improve factual accuracy
* support enterprise knowledge bases

---

# Request Lifecycle

Typical API request

```text
Client

↓

REST / WebSocket

↓

FastAPI Controller

↓

Validation

↓

Application Service

↓

Repository / AI Service

↓

Database or Provider

↓

Response

↓

Frontend
```

Controllers remain thin.

Business logic belongs to services.

Persistence belongs to repositories.

---

# Data Flow

The application continuously processes several streams of information.

Primary flows

* Audio
* Transcript
* AI Responses
* Events
* Database Writes
* UI Updates

Each flow should remain independent whenever possible.

---

# Security Architecture

Security principles

* Local-first
* Least privilege
* Secure by default
* Explicit consent
* Provider isolation

Sensitive information should remain local unless the user explicitly enables cloud providers.

Secrets must never be stored in source code.

---

# Configuration Flow

Configuration sources

```text
Environment Variables
        │
        ▼
Configuration Loader
        │
        ▼
Settings Service
        │
        ▼
Application Modules
```

Configuration should be centralized.

Modules must not read environment variables directly.

---

# Error Handling

Errors should propagate through structured exception types.

Guidelines

* fail fast
* log meaningful context
* avoid leaking sensitive data
* recover gracefully where possible

Unexpected exceptions should never crash the desktop application.

---

# Observability

The system should expose sufficient information for debugging and monitoring.

Recommended signals

* Logs
* Metrics
* Health checks
* Timing information
* AI latency
* Database latency
* WebSocket status

---

# Logging Strategy

Use structured logging.

Every important operation should include

* timestamp
* module
* operation
* duration (if applicable)
* log level
* correlation identifier

Avoid logging sensitive meeting content.

---

# Performance Strategy

Primary goals

* low latency
* predictable response times
* efficient memory usage
* responsive user interface

Optimization priorities

1. Correctness
2. Simplicity
3. Measurement
4. Optimization

Premature optimization should be avoided.

---

# Scalability

Although version 1 targets a single desktop user, the architecture should support future expansion.

Potential evolution

```text
SQLite
        │
        ▼
PostgreSQL

Local Models
        │
        ▼
Cloud Providers

Single User
        │
        ▼
Enterprise Teams

Local Files
        │
        ▼
Cloud Synchronization
```

Scalability should be achieved through abstraction layers rather than major architectural rewrites.

---

# Deployment Architecture

Development

```text
Docker Compose

↓

Frontend

↓

Backend

↓

SQLite
```

Production (Desktop)

```text
Tauri

↓

React

↓

FastAPI

↓

SQLite

↓

Optional AI Providers
```

Enterprise deployments may introduce PostgreSQL and additional infrastructure without changing core application logic.

---

# Technology Boundaries

Each technology has a clearly defined responsibility.

| Technology         | Responsibility           |
| ------------------ | ------------------------ |
| Tauri              | Desktop shell            |
| React              | User interface           |
| FastAPI            | Application backend      |
| SQLAlchemy         | Persistence abstraction  |
| SQLite             | Local storage            |
| Alembic            | Schema migrations        |
| Faster-Whisper     | Speech recognition       |
| Silero VAD         | Voice activity detection |
| FAISS              | Vector search            |
| Provider Interface | LLM abstraction          |

Technologies should not exceed their intended responsibilities.

---

# Layer Dependency Rules

The backend follows a dependency direction that protects domain and application
code from framework and vendor coupling.

```text
API / Presentation
        ↓
Application
        ↓
Domain

Infrastructure ──implements──► Domain contracts
Core / Composition Root ──────► wires all layers
```

Rules:

* `api` may depend on application services and API dependency providers.
* `application` may depend on domain types and domain contracts, never concrete
  infrastructure adapters.
* `domain` depends only on the Python standard library and domain code.
* `infrastructure` implements domain contracts and may depend on external SDKs,
  persistence libraries, or provider APIs.
* `core` provides configuration, logging, and composition utilities; it contains
  no business rules.
* `main` is the composition boundary and is the only place allowed to assemble
  concrete implementations across layers.

---

# Dependency Rules

The following dependencies are forbidden:

* Domain code importing FastAPI, SQLAlchemy, provider SDKs, or infrastructure
  implementations.
* Application services importing concrete database, vector-store, or AI-provider
  adapters.
* API routes querying databases or invoking providers directly.
* Infrastructure adapters containing business decisions.
* Modules reading environment variables directly instead of using centralized
  settings.

Tests may compose concrete implementations or substitutes explicitly. Production
code must receive dependencies through constructors or narrow API dependency
providers; it must not use a global service locator.

---

# Application Lifecycle

The FastAPI application is created through an application factory. For each
application instance, the lifecycle is:

```text
Resolve Settings
        ↓
Create Container and configure logging
        ↓
Store container on app.state.container
        ↓
await container.start()
        ↓
Serve requests
        ↓
await container.stop()
```

The asynchronous lifespan boundary owns startup and shutdown. It ensures that
future managed resources—such as database connections, provider clients, and
background workers—are started before traffic is served and released during
shutdown.

---

# Request Flow

HTTP and WebSocket endpoints remain thin adapters around application use cases.

```text
Client
        ↓
FastAPI route
        ↓
API dependency provider
        ↓
Application command or query service
        ↓
Domain contracts and rules
        ↓
Infrastructure adapter (when required)
        ↓
Response or event
```

Routes validate transport data and translate responses. They do not contain
business rules, direct persistence access, or provider-specific calls.

---

# AI Processing Flow

AI processing remains modular and event-oriented so individual stages can be
replaced without changing business workflows.

```text
Audio capture
        ↓
Voice activity detection
        ↓
Speech recognition
        ↓
Transcript buffer ──► Persistence
        ↓
Language detection and translation
        ↓
German simplification
        ↓
Context building and RAG retrieval
        ↓
Reply coaching, summaries, action items, and vocabulary extraction
        ↓
Persistence and UI events
```

Provider implementations sit behind domain contracts. Stages publish immutable
events where asynchronous or cross-module coordination is needed.

---

# Naming Conventions

* Packages, modules, functions, and variables use `snake_case`.
* Classes, protocols, and enums use `PascalCase`.
* Constants use `UPPER_CASE`.
* Application service methods use intent-revealing verbs, such as
  `generate_summary` or `find_active_meeting`.
* Logger names are module-qualified and derived from `__name__`.
* Repository and provider contracts use descriptive nouns; concrete adapters
  identify the technology they integrate with.

---

# Composition Root

`app.core.container.Container` is the lightweight composition root. Its
constructor receives validated `Settings`, configures logging, and will
explicitly construct providers, repositories, and application services as their
interfaces are introduced. It exposes factory methods rather than public
singleton attributes and has asynchronous `start` and `stop` lifecycle hooks.

`app.main.create_app` creates one container per FastAPI application instance and
stores it on `app.state.container`. Future FastAPI dependencies must retrieve
narrow services from that container rather than construct infrastructure inside
routes. No global container is permitted.

---

# Future Evolution

The architecture is designed to accommodate future capabilities such as

* multi-user support
* enterprise deployment
* cloud synchronization
* organization knowledge bases
* calendar integrations
* video meeting integrations
* plugin ecosystem
* mobile companion application

These additions should extend the architecture rather than replace it.

---

# Architecture Summary

The AI Meeting Copilot architecture emphasizes

* modularity
* explicit interfaces
* event-driven communication
* provider independence
* local-first processing
* privacy by design
* maintainability
* scalability
* testability

Every implementation should reinforce these principles.

---
