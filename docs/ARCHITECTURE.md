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
