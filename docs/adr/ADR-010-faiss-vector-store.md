# ADR-010: Use FAISS as the Default Vector Store

## Status

Accepted

## Date

2026-07-22

---

# Context

Retrieval-Augmented Generation (RAG) requires efficient similarity search over document embeddings.

The initial deployment targets local desktop environments where simplicity, speed, and offline support are essential.

---

# Decision

Use FAISS as the default vector store.

Vector operations must be accessed through a dedicated Vector Repository abstraction.

---

# Architecture

```
Knowledge Documents
        │
        ▼
Chunking
        │
        ▼
Embedding Generation
        │
        ▼
FAISS Index
        │
        ▼
Retriever
        │
        ▼
LLM Context Builder
```

---

# Alternatives Considered

## pgvector

Advantages

- Integrated with PostgreSQL
- SQL-based queries

Disadvantages

- Requires PostgreSQL
- Not ideal for local desktop-first deployments

Deferred for future enterprise deployments.

---

## Qdrant

Advantages

- Dedicated vector database
- Rich filtering

Disadvantages

- Additional service to manage
- Higher operational complexity

Rejected for version 1.

---

## Milvus

Advantages

- Highly scalable

Disadvantages

- Infrastructure overhead
- Better suited for distributed systems

Rejected for version 1.

---

# Rationale

FAISS provides

- excellent similarity search performance
- local execution
- mature ecosystem
- no additional infrastructure
- straightforward integration with Python

---

# Benefits

- Fast retrieval
- Offline capability
- Low operational complexity
- Easy distribution with desktop application

---

# Risks

FAISS metadata support is limited compared to dedicated vector databases.

Mitigation

Store metadata in the relational database and keep only vector indexes in FAISS.

---

# Future

The Vector Repository abstraction should allow migration to

- pgvector
- Qdrant
- Milvus
- Pinecone (optional)

without changing retrieval services.

---

# Related Documents

- 05-ai-design.md
- 08-database-design.md

---

# Decision

Accepted