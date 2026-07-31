# Contributing Guide

Welcome to AI Meeting Copilot.

Thank you for contributing to this project.

Our goal is to build a maintainable, privacy-first, enterprise-grade AI application that can evolve over many years.

Please read this guide before opening an issue or submitting a pull request.

---

# Before You Start

Read the documentation in the following order:

1. docs/README.md
2. docs/SUMMARY.md
3. Project Principles
4. Product Vision
5. System Architecture
6. Project Structure
7. Coding Standards
8. AI Design
9. Development Guide
10. API Design
11. Database Design

Do not start implementation before understanding the architecture.

---

# Core Principles

Every contribution should improve at least one of the following:

* Maintainability
* Readability
* Performance
* Privacy
* Testability
* Developer Experience

Never sacrifice architecture for short-term convenience.

---

# Development Workflow

1. Create or select an issue.
2. Create a feature branch.
3. Implement the change.
4. Add or update tests.
5. Update documentation if behavior changes.
6. Run quality checks.
7. Open a Pull Request.

---

# Local Development Infrastructure

Use uv and Python 3.12 for all local development.

```bash
uv sync --all-groups
uv run pre-commit install
```

Run the same quality checks used by CI before opening a pull request.

```bash
uv run ruff check backend tests
uv run black --check backend tests
uv run mypy backend/app
uv run pytest
```

Pre-commit runs only fast formatting, linting, and file-hygiene checks. Type
checking and tests run in CI and should be run locally when preparing a pull
request.

---

# Branch Naming

Examples

* feature/realtime-translation
* feature/rag-indexing
* bugfix/websocket-reconnect
* docs/api-design
* refactor/provider-interface

---

# Commit Messages

Follow Conventional Commits.

Examples

* feat: add meeting summary service
* fix: prevent duplicate transcript events
* docs: update architecture diagrams
* refactor: simplify provider interface
* test: add repository integration tests
* chore: upgrade dependencies

---

# Pull Requests

Every Pull Request should include

* purpose
* implementation summary
* testing performed
* documentation updates
* known limitations

Small Pull Requests are preferred over large ones.

---

# Code Quality

Contributors should

* follow SOLID principles
* write small functions
* avoid duplicated logic
* use meaningful names
* prefer composition over inheritance
* keep modules focused

---

# Testing Requirements

Every feature should include appropriate tests.

Preferred testing pyramid

* Unit Tests
* Integration Tests
* End-to-End Tests

Changes without tests require a clear justification.

---

# Documentation

Documentation is part of the source code.

Update documentation when

* behavior changes
* architecture changes
* APIs change
* configuration changes
* dependencies change

---

# AI Contributions

AI-generated code is welcome.

However,

* understand the generated code
* review every change
* never merge blindly
* ensure architectural compliance

Generated code must meet the same quality standards as handwritten code.

---

# Dependencies

Before introducing a dependency, consider

* maintenance
* security
* license
* community support
* long-term viability
* necessity

Avoid adding dependencies for trivial functionality.

---

# Security

Never commit

* API keys
* passwords
* tokens
* certificates
* secrets
* personal data

Use environment variables or secure storage.

---

# Performance

Consider

* startup time
* memory usage
* CPU utilization
* GPU utilization
* latency

Optimize only after measuring.

---

# Review Checklist

Before requesting review, verify

* Code builds successfully.
* Tests pass.
* No dead code remains.
* Documentation is updated.
* Logging is appropriate.
* Configuration is documented.
* Naming is consistent.
* Architecture is respected.

---

# Communication

Be respectful.

Assume positive intent.

Focus discussions on technical decisions and evidence.

Constructive feedback is encouraged.

---

# Definition of Done

A contribution is complete only if

* implementation is correct
* tests pass
* documentation is updated
* architecture remains consistent
* code review comments are addressed
* no regressions are introduced

---

# Thank You

Every contribution helps make AI Meeting Copilot more reliable, maintainable, and useful for its users.

We appreciate your time and expertise.
