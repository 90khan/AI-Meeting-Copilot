# ADR-003: SQLite First, PostgreSQL Later

## Status

Accepted

## Date

2026-07-22

---

# Context

Version 1 is a desktop application.

Primary requirements

- offline operation
- zero configuration
- simple installation
- local privacy

---

# Decision

Version 1 uses SQLite.

Future enterprise deployments may use PostgreSQL.

---

# Why SQLite

Advantages

- embedded
- reliable
- zero administration
- excellent local performance

---

# Migration Strategy

Application

↓

Repository

↓

SQLAlchemy

↓

SQLite / PostgreSQL

Business logic must never know which database engine is used.

---

# Risks

Concurrent write limitations.

Mitigation

Desktop usage pattern makes this acceptable.

---

# Future

Migration to PostgreSQL should require only configuration changes and Alembic migrations.

---

# Decision

Accepted