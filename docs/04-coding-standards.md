# AI Meeting Copilot

# Coding Standards

Version: 1.0

Status: Active

---

# Purpose

This document defines the coding standards for the AI Meeting Copilot project.

Every contributor, including AI coding agents, must follow these standards.

Code quality has higher priority than development speed.

Readable code is preferred over clever code.

---

# Core Principles

Always prefer

- readability
- maintainability
- simplicity
- consistency
- explicitness

Avoid

- magic
- hidden dependencies
- duplicated logic
- premature optimization

---

# General Rules

Code should be

- modular
- testable
- documented
- deterministic
- observable

---

# SOLID Principles

Every service must follow SOLID.

Single Responsibility

Every class has exactly one reason to change.

---

Open / Closed

New behavior should be added through extension.

Avoid modifying existing code.

---

Liskov

Derived classes must behave like base classes.

---

Interface Segregation

Small interfaces.

Never create giant interfaces.

---

Dependency Inversion

Depend on abstractions.

Never depend directly on implementations.

---

# Clean Architecture

Dependency direction

Presentation

↓

Application

↓

Domain

↓

Infrastructure

Never reverse dependencies.

---

# File Size

Recommended

<300 lines

Maximum

500 lines

If a file grows larger,

split it.

---

# Function Size

Preferred

10–25 lines

Maximum

50 lines

Long functions usually indicate multiple responsibilities.

---

# Class Size

Prefer

100–200 lines

Maximum

400 lines

Large classes should be decomposed.

---

# Function Rules

Every function should

Do one thing.

Return one result.

Be easy to test.

Avoid side effects.

---

Good

detect_question()

Bad

detect_question_and_translate_and_save()

---

# Naming

Variables

Descriptive

Examples

transcript

meeting_summary

speaker_name

Never

x

tmp

data2

abc

---

Functions

Use verbs.

Examples

translate()

summarize()

generate_reply()

store_vocabulary()

---

Classes

Use nouns.

Examples

SpeechService

TranscriptBuffer

MeetingSession

VocabularyRepository

---

Constants

UPPER_CASE

---

Booleans

Prefix

is_

has_

can_

should_

Examples

is_enabled

has_audio

can_translate

---

# Async First

Network operations

must be async.

File operations

should be async when possible.

AI requests

must be async.

Blocking code is discouraged.

---

# Type Hints

Required.

Every public function.

Every service.

Every interface.

Example

```python
def translate(
    text: str,
    language: str
) -> TranslationResult:
```

---

# Docstrings

Public classes

required.

Public methods

required.

Private methods

optional.

Google Style preferred.

---

Example

```python
def translate(text: str) -> str:
    """
    Translate text into the configured language.

    Args:
        text: Input sentence.

    Returns:
        Translated sentence.
    """
```

---

# Comments

Explain

WHY

not

WHAT

Bad

```python
i += 1
# increase i
```

Good

```python
# Skip duplicated transcript fragments.
```

---

# Logging

Never

print()

Use logging.

Levels

DEBUG

INFO

WARNING

ERROR

CRITICAL

Every exception should be logged.

---

# Exceptions

Never swallow exceptions.

Never

```python
except:
    pass
```

Always

```python
except Exception as exc:
    logger.exception(exc)
```

Use custom exceptions.

Examples

TranslationError

SpeechRecognitionError

MeetingError

ProviderError

---

# Configuration

Never hardcode

API Keys

Languages

Model Names

Timeouts

URLs

Everything configurable.

---

# Dependency Injection

Services should receive dependencies.

Bad

```python
service = OpenAI()
```

Good

```python
service = llm_provider
```

---

# Circular Dependencies

Forbidden.

---

# Global Variables

Avoid.

Configuration only.

---

# Event Driven

Business modules communicate through events.

Avoid direct coupling.

---

# Provider Agnostic

Never

OpenAIService

inside

ReplyCoach

Instead

LLMProvider

↓

OpenAI

Anthropic

Gemini

Ollama

---

# AI Prompt Rules

Never build prompts inline.

Bad

```python
prompt = f"..."
```

Good

PromptManager

↓

Prompt Templates

↓

Variables

↓

LLM

---

# Prompt Storage

```
backend/prompts/

translation.md

summary.md

reply_coach.md

grammar.md
```

---

# Structured Outputs

Prefer JSON outputs.

Bad

Free-form text.

Good

```json
{
  "question": "...",
  "confidence": 0.94
}
```

---

# Repository Pattern

Database access only inside repositories.

Never

Service

↓

SQL

---

# Testing

Every service

Unit Test

Every workflow

Integration Test

Every release

End-to-End Test

---

Coverage Target

80%

Minimum

70%

---

# Performance

Avoid

nested loops

unnecessary copies

blocking calls

large objects

---

# Security

Never log

API Keys

Tokens

Passwords

Meeting Audio

Personal Data

---

# Git

Branch Names

feature/

bugfix/

refactor/

docs/

test/

---

Commit Messages

Conventional Commits

Examples

feat:

fix:

refactor:

docs:

test:

---

# Pull Requests

Every PR should include

Purpose

Changes

Tests

Screenshots (if UI)

Checklist

---

# Documentation

Every module

README.md

Every service

docstrings

Every API

OpenAPI

---

# Health Checks

Every major service exposes

health()

---

# Code Review Checklist

Readable

Small

Tested

Typed

Logged

Documented

Configurable

Modular

Observable

No duplication

No dead code

No TODO left behind

---

# Forbidden

God Classes

God Functions

Static Singletons

Hidden Globals

Magic Numbers

Magic Strings

Circular Imports

Hardcoded Secrets

Copy Paste Programming

---

# Preferred Patterns

Strategy

Factory

Builder

Adapter

Repository

Dependency Injection

Observer

Pipeline

State

Command

Plugin

---

# Avoid

Massive Inheritance

Deep Nesting

Utility Classes

Boolean Flags controlling behavior

Long parameter lists

---

# AI Coding Guidelines

When generating code

Prefer

clarity

modularity

small commits

typed interfaces

independent services

Do not

generate speculative features

introduce unnecessary abstractions

ignore existing architecture

break dependency rules

---

# Definition of Excellent Code

Excellent code is

Easy to read

Easy to test

Easy to replace

Easy to extend

Easy to debug

Easy to document

Easy to delete

If removing a feature is difficult,

the architecture is probably wrong.

---

# Final Principle

Write code for humans first.

AI is only the second reader.
