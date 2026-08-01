# AI Meeting Copilot

# Development Guide

Version: 1.0

Status: Active

---

# Purpose

This document defines the development workflow for AI Meeting Copilot.

Its goal is to ensure that every contributor—including AI coding agents—follows the same engineering process.

Consistency is more important than speed.

---

# Development Philosophy

Every change should make the project

- easier to understand
- easier to maintain
- easier to extend
- easier to test

Never sacrifice long-term quality for short-term speed.

---

# Engineering Values

We value

- Clean Architecture
- SOLID Principles
- Small Modules
- Strong Typing
- Automated Testing
- Documentation
- Observability
- Privacy
- Performance

---

# Development Workflow

Every feature follows the same lifecycle.

```
Idea

↓

Discussion

↓

Architecture Review

↓

Task Breakdown

↓

Implementation

↓

Unit Tests

↓

Integration Tests

↓

Manual Verification

↓

Documentation Update

↓

Pull Request

↓

Review

↓

Merge
```

Never skip testing or documentation.

---

# Definition of Ready

A task is ready only if

- requirements are clear
- affected modules are identified
- dependencies are known
- acceptance criteria exist

If any of these are missing,

clarify before writing code.

---

# Definition of Done

A task is complete only if

- implementation works
- tests pass
- documentation updated
- logs added
- configuration reviewed
- architecture respected
- no TODOs remain
- no dead code introduced

---

# Branch Strategy

Use Git Flow–inspired naming.

Examples

```
feature/live-translation

feature/reply-coach

feature/rag-memory

bugfix/transcript-buffer

refactor/provider-interface

docs/api-design

test/context-manager
```

Avoid long-lived branches.

---

# Commit Messages

Use Conventional Commits.

Examples

```
feat: add translation service

fix: resolve transcript duplication

refactor: simplify provider interface

docs: update AI design

test: add summary regression tests
```

Each commit should represent one logical change.

---

# Pull Requests

A pull request should be small and focused.

Include

- Purpose
- Implementation Summary
- Testing Performed
- Risks
- Screenshots (if UI)
- Documentation Changes

Large PRs are harder to review and should be avoided.

---

# Feature Development Process

Every feature should follow these steps.

1. Understand the requirement.
2. Identify the owning module.
3. Review related documentation.
4. Design the public interface.
5. Implement the feature.
6. Write tests.
7. Update documentation.
8. Verify manually.
9. Submit PR.

---

# Module Ownership

Every feature belongs to exactly one primary module.

Examples

Speech Recognition

→ Speech Service

Translation

→ Translation Service

Reply Suggestions

→ Reply Coach

Vocabulary Extraction

→ Vocabulary Service

Avoid splitting one feature across unrelated modules.

---

# Architecture Review Checklist

Before implementing a feature, ask:

- Does this belong in an existing module?
- Does it introduce unnecessary coupling?
- Can it be event-driven?
- Is it configurable?
- Is it testable?
- Is it provider-agnostic?

If the answer is "no" to any of these, redesign before coding.

---

# Adding a New Feature

When introducing a new capability

1. Create or extend the correct module.
2. Define public interfaces.
3. Add configuration if needed.
4. Emit domain events.
5. Add logging.
6. Add tests.
7. Update documentation.

Never add functionality without updating documentation.

---

# Refactoring Guidelines

Refactoring should improve

- readability
- maintainability
- modularity
- testability

Refactoring should not change observable behavior.

If behavior changes,

it is no longer a refactor.

---

# Dependency Management

Before adding a dependency ask:

- Is it actively maintained?
- Is the license compatible?
- Can existing libraries solve the problem?
- Is the dependency worth its complexity?

Prefer fewer dependencies.

---

# Error Handling

Every error should be

- classified
- logged
- recoverable when possible

Never suppress exceptions silently.

---

# Logging

Every important workflow should produce structured logs.

Examples

- Meeting started
- Transcript received
- Translation completed
- Summary generated
- Provider switched

Logs should help diagnose problems without exposing sensitive information.

---

# Configuration

All configurable values belong in configuration files or environment variables.

Examples

- model names
- API keys
- thresholds
- retry counts
- language preferences

Never hardcode environment-specific values.

---
---

# Testing Strategy

Testing is mandatory.

Every feature must include automated tests.

Testing pyramid

```
          E2E
        Integration
      Unit Tests
```

Priority

1. Unit Tests
2. Integration Tests
3. End-to-End Tests

---

# Unit Testing

Every service should have unit tests.

Requirements

- deterministic
- isolated
- fast
- repeatable

Mock external systems.

Never call real AI providers during unit tests.

---

# Integration Testing

Integration tests verify communication between modules.

Examples

- Speech → Translation
- Translation → Reply Coach
- Memory → Summary
- API → Database

Integration tests may use test containers.

---

# End-to-End Testing

E2E tests simulate real user workflows.

Example

```
Start Meeting

↓

Speak German

↓

Receive Transcript

↓

Translation

↓

Reply Suggestion

↓

Meeting Summary

↓

Export Notes
```

Every critical workflow should have at least one E2E test.

---

# AI Testing

Traditional testing is not sufficient.

AI components require additional validation.

Recommended categories

- Prompt Regression Tests
- Structured Output Tests
- Retrieval Tests
- Hallucination Tests
- Latency Tests

AI quality should be measured continuously.

---

# Performance Testing

Performance tests should measure

- speech latency
- translation latency
- prompt execution
- retrieval speed
- UI responsiveness
- memory usage

Target values are defined in the System Architecture document.

---

# Continuous Integration

The Backend CI workflow runs on pushes, pull requests, and manual dispatch. It
uses uv, Python 3.12, and the locked dependency set.

Every CI run executes, in order:

```bash
uv sync --locked --all-groups
uv run ruff check backend tests
uv run black --check backend tests
uv run mypy backend/app
uv run pytest
```

Pre-commit is intentionally limited to fast file-hygiene, Ruff, and Black
checks. Type checking and tests remain CI quality gates.

A Pull Request must not be merged if CI fails.

---

# Continuous Delivery

Deployment pipeline

```
Commit

↓

Pull Request

↓

CI

↓

Review

↓

Merge

↓

Build

↓

Release
```

Release builds should be reproducible.

---

# Code Review

Every Pull Request should be reviewed.

Reviewers should verify

- architecture
- readability
- naming
- tests
- documentation
- logging
- security
- performance

Focus on correctness before style.

---

# Review Checklist

Before approving

✓ Feature works

✓ No duplicated code

✓ Tests added

✓ Documentation updated

✓ Logging included

✓ Configuration supported

✓ Dependency rules respected

✓ Small readable functions

---

# Documentation Rules

Documentation is part of the codebase.

Whenever behavior changes

update documentation.

Required documentation

- README
- Architecture
- API
- Database
- AI Design

Documentation must remain synchronized with implementation.

---

# Versioning

Use Semantic Versioning.

Examples

```
1.0.0

1.1.0

1.2.3

2.0.0
```

Rules

Major

Breaking changes

Minor

New functionality

Patch

Bug fixes

---

# Release Process

Every release should include

- changelog
- version bump
- release notes
- regression tests
- smoke tests

Never release untested code.

---

# Bug Management

Every bug should include

- description
- reproduction steps
- expected behavior
- actual behavior
- logs
- screenshots if applicable

Critical bugs require regression tests before closing.

---

# Sprint Planning

Each sprint should define

- goals
- priorities
- risks
- acceptance criteria

Avoid starting work without clear acceptance criteria.

---

# Backlog Management

Organize work into

- Features
- Improvements
- Technical Debt
- Bugs
- Research

Review backlog regularly.

---

# Database Migrations

Alembic manages all database schema changes. Run migration commands from the
repository root so Alembic can load `alembic.ini` and the configured backend
package path.

Create a migration after updating ORM metadata:

```bash
uv run alembic revision --autogenerate -m "describe schema change"
```

Upgrade the configured database to the latest revision:

```bash
uv run alembic upgrade head
```

Downgrade the configured database by one revision:

```bash
uv run alembic downgrade -1
```

Autogenerated migrations are a draft, not an approval. Review every generated
operation, constraint, and downgrade before committing it. Migrations must not
contain seed data or application business logic.

---

# Technical Debt

Technical debt should be tracked explicitly.

Never hide technical debt inside TODO comments.

Use issue tracking instead.

---

# AI Development Workflow

Every AI feature follows the same process.

```
Idea

↓

Prompt Design

↓

Model Selection

↓

Prototype

↓

Evaluation

↓

Regression Tests

↓

Integration

↓

Documentation
```

Prompt quality should be evaluated before implementation.

---

# Prompt Development

Every prompt should have

- purpose
- variables
- expected output
- version
- regression tests

Prompt changes should be reviewed like source code.

---

# Codex Workflow

When using Codex

1. Read all documentation.
2. Understand architecture.
3. Implement only the requested scope.
4. Write tests.
5. Update documentation.
6. Avoid speculative features.
7. Respect dependency rules.

Codex should never bypass documented architecture.

---

# Security Checklist

Before merging

✓ No secrets committed

✓ Inputs validated

✓ Sensitive logs removed

✓ Dependencies reviewed

✓ Error handling verified

✓ Configuration checked

---

# Privacy Checklist

Verify

- meeting data encryption
- minimal data retention
- user-controlled deletion
- local processing when possible
- provider compliance

Privacy is a release requirement.

---

# Production Readiness Checklist

Before every release

✓ Tests pass

✓ CI green

✓ Documentation updated

✓ Logs reviewed

✓ Monitoring enabled

✓ Performance acceptable

✓ Security reviewed

✓ Version updated

✓ Release notes prepared

---

# Engineering Principles

The project should always remain

- modular
- testable
- observable
- maintainable
- extensible

Engineering quality has higher priority than delivery speed.

---

# Final Principle

Every line of code should make the project easier to understand tomorrow than it was yesterday.

The best feature is not the one implemented fastest.

The best feature is the one that future engineers can confidently maintain.

---
