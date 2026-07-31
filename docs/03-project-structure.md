# AI Meeting Copilot

# Project Structure

Version: 1.0

---

# Purpose

This document defines the directory structure, module responsibilities, dependency boundaries, and organizational principles of the AI Meeting Copilot project.

Every file should have a clear responsibility.

No folder should become a "miscellaneous" container.

---

# Repository Overview

```
ai-meeting-copilot/

├── docs/
├── frontend/
├── backend/
├── desktop/
├── shared/
├── scripts/
├── docker/
├── tests/
├── examples/
├── assets/
├── .github/
├── .env.example
├── docker-compose.yml
├── README.md
└── LICENSE
```

---

# Root Directories

## docs/

Contains all technical documentation.

Examples

- Architecture
- Coding Standards
- API Design
- Database Design
- ADRs

No implementation code.

---

## frontend/

Contains the React application.

Responsibilities

- UI
- Overlay
- Settings
- Meeting Review
- Learning Dashboard

No business logic.

---

## backend/

Contains all backend services.

Responsible for

- AI Pipeline
- Business Logic
- APIs
- RAG
- Storage
- Event Processing

---

## desktop/

Contains the desktop wrapper.

Responsibilities

- Window Management
- Tray Icon
- Native APIs
- Audio Permissions
- Global Shortcuts

---

## shared/

Shared code.

Examples

- DTOs
- Interfaces
- Constants
- Event Definitions
- Validation Schemas

---

## tests/

Contains all automated tests.

Never mix tests with production code.

---

## scripts/

Development utilities.

Examples

- Download Models
- Create Database
- Benchmark
- Data Migration

---

## docker/

Container definitions.

Dockerfiles

Compose Files

Development Containers

---

## assets/

Static resources.

Icons

Images

Fonts

Sounds

---

# Backend Structure

```
backend/

api/
config/
core/
database/
events/
logging/
middleware/
models/
repositories/
services/
providers/
plugins/
workers/
utils/
tests/
```

---

# api/

REST API

WebSocket Endpoints

Request Validation

Response Models

No business logic.

---

# config/

Application configuration.

Examples

settings.py

logging.py

providers.py

feature_flags.py

---

# core/

Core abstractions.

Examples

Interfaces

Base Classes

Dependency Injection

Service Registry

Pipeline Engine

---

# database/

Database layer.

Contains

Connection

Migrations

Repositories

Models

Never expose SQL outside this layer.

---

# events/

Event Bus

Event Definitions

Publishers

Subscribers

Dispatchers

---

# logging/

Logging configuration.

Metrics.

Tracing.

---

# middleware/

Cross-cutting concerns.

Authentication

Rate Limiting

Error Handling

Timing

Logging

---

# models/

Domain Models.

Meeting

Transcript

Vocabulary

Session

User Preferences

Summary

---

# repositories/

Data access only.

Repositories never contain business rules.

---

# services/

Contains business logic.

Every feature is implemented as an independent service.

---

Example

```
services/

speech/

translation/

simplifier/

question_detection/

reply_coach/

summary/

memory/

rag/

vocabulary/

meeting/

settings/
```

---

# providers/

External providers.

Examples

OpenAI

Anthropic

Gemini

Ollama

LM Studio

Whisper

Future providers

---

# plugins/

Plugin implementations.

Plugins are loaded dynamically.

---

# workers/

Background jobs.

Examples

Meeting Export

Summary Generation

Vocabulary Analysis

Cleanup

---

# utils/

Utility functions.

Keep this directory small.

If a utility grows large,

promote it into its own module.

---

# Frontend Structure

```
frontend/

src/

components/

features/

hooks/

layouts/

pages/

services/

stores/

types/

utils/

styles/

assets/
```

---

# components/

Reusable UI.

Buttons

Cards

Dialogs

Tables

Inputs

Panels

---

# features/

Feature-based organization.

Examples

assist/

meeting_review/

learning/

settings/

history/

---

# hooks/

Custom React hooks.

Examples

useMeeting()

useTranscript()

useSettings()

---

# layouts/

Application layouts.

Desktop

Overlay

Fullscreen

Compact

---

# pages/

Application pages.

Dashboard

History

Settings

Learning

Review

---

# services/

Frontend service layer.

API Client

WebSocket Client

Settings Client

---

# stores/

Global state.

Meeting Store

Settings Store

Overlay Store

History Store

---

# types/

Shared frontend types.

---

# styles/

Global styling.

Themes

Typography

Animations

Spacing

---

# Desktop Structure

```
desktop/

src/

commands/

permissions/

windows/

tray/

notifications/
```

---

# Shared Structure

```
shared/

events/

contracts/

schemas/

constants/

types/
```

Shared code must never depend on backend or frontend.

---

# Module Rules

Every module must contain

```
module/

README.md

service.py

models.py

schemas.py

events.py

exceptions.py

tests/
```

Small modules may omit unnecessary files.

---

# Naming Conventions

Directories

snake_case

Examples

speech_recognition

reply_coach

meeting_summary

---

Classes

PascalCase

Examples

SpeechService

MeetingManager

TranscriptBuffer

---

Functions

snake_case

Examples

generate_summary()

translate_text()

detect_question()

---

Constants

UPPER_CASE

---

# Dependency Rules

Allowed

Presentation

↓

Application

↓

Domain

↓

Infrastructure

Forbidden

UI

↓

Database

Service

↓

React

Repository

↓

Provider

---

# Import Rules

Never use circular imports.

Prefer dependency injection.

Avoid static singletons.

---

# Feature Organization

Every major capability owns its code.

Example

reply_coach/

contains

API

Service

Events

Tests

Models

Configuration

No feature should be spread across unrelated folders.

---

# Configuration Files

```
config/

application.yaml

providers.yaml

logging.yaml

features.yaml
```

---

# Environment Variables

Stored only in

.env

.env.local

.env.production

Never commit secrets.

---

# Documentation

Every module should include

README.md

Describing

Purpose

Dependencies

Events

Public APIs

Configuration

---

# Testing Structure

```
tests/

unit/

integration/

e2e/

performance/
```

---

# Definition of Done

A module is complete only if it has

✓ documentation

✓ tests

✓ logging

✓ configuration

✓ error handling

✓ health checks

✓ typed interfaces

✓ event definitions (if applicable)

---

# Future Expansion

The structure should support future additions without refactoring.

Examples

Grammar Coach

Presentation Coach

Interview Mode

Voice Commands

Company RAG

Meeting Analytics

Offline AI

Mobile Companion

These features should fit naturally into the existing directory structure.

---

# Summary

The project structure prioritizes

- clarity
- modularity
- scalability
- maintainability
- testability

Every directory has a single responsibility.

Every module is independently testable.

Every future feature should integrate without disrupting the existing architecture.