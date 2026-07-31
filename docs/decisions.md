# Architecture Decisions Index

This document summarizes the major engineering decisions made for AI Meeting Copilot.

Detailed records are available in the ADR directory.

| ID      | Decision                       | Status   |
| ------- | ------------------------------ | -------- |
| ADR-001 | Use Tauri instead of Electron  | Accepted |
| ADR-002 | FastAPI as backend             | Accepted |
| ADR-003 | SQLite first, PostgreSQL later | Accepted |
| ADR-004 | Event-driven architecture      | Accepted |
| ADR-005 | Provider abstraction layer     | Accepted |
| ADR-006 | Local-first AI processing      | Accepted |
| ADR-007 | Repository Pattern             | Accepted |
| ADR-008 | SQLAlchemy + Alembic           | Accepted |
| ADR-009 | Faster-Whisper as default STT  | Accepted |
| ADR-010 | FAISS as default vector store  | Accepted |

## Decision Process

Before introducing a significant technical change

1. Create a new ADR.
2. Describe alternatives.
3. Explain trade-offs.
4. Record the decision.
5. Reference the ADR in the related Pull Request.

Architecture decisions should never exist only in code reviews or chat conversations.
