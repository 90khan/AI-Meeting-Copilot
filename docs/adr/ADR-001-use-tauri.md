# ADR-001: Use Tauri Instead of Electron

## Status

Accepted

## Date

2026-07-22

## Decision Makers

Project Maintainers

---

# Context

AI Meeting Copilot is a desktop-first application that continuously performs:

- speech recognition
- real-time translation
- AI inference
- overlay rendering
- local document indexing
- background processing

These workloads already consume CPU, memory, and GPU resources.

The desktop framework should therefore minimize its own runtime overhead.

The project also prioritizes:

- low memory usage
- native operating system integration
- fast startup
- strong security
- small installation size

---

# Decision

The desktop application will use **Tauri**.

React will be used for the frontend.

Rust will be used only for native desktop functionality provided by Tauri.

Business logic remains inside the Python backend.

---

# Alternatives Considered

## Electron

Advantages

- mature ecosystem
- large community
- many plugins
- extensive documentation

Disadvantages

- high RAM usage
- Chromium bundled with every application
- larger executable size
- higher idle CPU usage

Decision

Rejected.

---

## Flutter Desktop

Advantages

- native rendering
- cross-platform

Disadvantages

- introduces Dart
- duplicates frontend technology
- weaker web ecosystem for this project

Decision

Rejected.

---

## Neutralino

Advantages

- extremely lightweight

Disadvantages

- smaller ecosystem
- fewer integrations
- less mature

Decision

Rejected.

---

# Rationale

Tauri provides the best balance between

- performance
- binary size
- security
- developer experience

Using React allows reuse of the frontend ecosystem while avoiding Electron's runtime cost.

---

# Consequences

## Positive

- significantly smaller binaries
- reduced memory usage
- faster startup
- native OS integration
- improved security defaults
- better battery usage on laptops

## Negative

- smaller ecosystem than Electron
- occasional Rust knowledge may be required
- fewer desktop plugins

---

# Risks

Rust-specific native functionality may require additional expertise.

Mitigation

Keep Rust usage minimal.

All business logic remains in Python.

---

# Implementation Notes

Desktop layer responsibilities

- window management
- tray icon
- notifications
- permissions
- global shortcuts
- overlay window

Business logic is never implemented in Tauri commands.

---

# Related Documents

- 02-system-architecture.md
- 03-project-structure.md
- 04-coding-standards.md

---

# Review Date

Revisit if

- Tauri development significantly slows
- Electron runtime improves substantially
- project requirements change

---

# Decision

Accepted