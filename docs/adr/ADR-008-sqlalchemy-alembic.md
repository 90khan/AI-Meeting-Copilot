# ADR-008: Use SQLAlchemy and Alembic for Persistence

## Status

Accepted

## Date

2026-07-22

---

# Context

The project requires

- typed models
- database portability
- schema migrations
- maintainable persistence
- support for SQLite and PostgreSQL

Managing SQL manually would become increasingly difficult as the schema evolves.

---

# Decision

Use

- SQLAlchemy 2.x as the ORM
- Alembic for schema migrations

---

# Rationale

SQLAlchemy provides

- mature ORM
- excellent async support
- strong typing
- multiple database backends
- active community

Alembic provides

- versioned migrations
- schema evolution
- rollback support
- production-ready workflows

---

# Migration Rules

Every schema change must

1. Create a new Alembic revision.
2. Be reviewed before merging.
3. Be committed alongside application code.
4. Include a descriptive revision name.

Manual changes to production databases are prohibited.

---

# Alternatives Considered

## Raw SQL

Advantages

- Maximum control

Disadvantages

- Boilerplate
- Harder maintenance
- Reduced portability

Rejected.

---

## Django ORM

Advantages

- Integrated tooling

Disadvantages

- Tight coupling to Django
- Unnecessary framework dependency

Rejected.

---

# Benefits

- Database portability
- Consistent schema evolution
- Strong developer tooling
- Better maintainability

---

# Risks

Improper auto-generated migrations may introduce unintended changes.

Mitigation

Review every migration manually before applying it.

---

# Related Documents

- 08-database-design.md
- 06-development-guide.md

---

# Decision

Accepted