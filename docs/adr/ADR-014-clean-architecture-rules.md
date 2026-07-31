# ADR-014: Enforce Clean Architecture Dependency Rules

## Status

Accepted

## Context

The project must remain provider-agnostic, testable, and maintainable as AI,
persistence, and desktop capabilities grow. Uncontrolled framework and vendor
imports would couple business behavior to replaceable infrastructure.

## Decision

Enforce inward dependencies: API depends on application, application depends on
domain contracts, and infrastructure implements domain contracts. The domain is
independent of FastAPI, SQLAlchemy, provider SDKs, and other infrastructure.
The composition root is the sole place that wires concrete implementations.

## Consequences

Positive:

* Providers, persistence technologies, and transport frameworks remain
  replaceable.
* Domain and application units are straightforward to test with fakes.
* Architectural violations are easy to recognize in imports and reviews.

Negative:

* Interfaces and adapter code add initial structure and files.
* Contributors must preserve the dependency direction as features evolve.

