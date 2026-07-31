# ADR-011: Use a Lightweight Custom DI Container

## Status

Accepted

## Context

The backend needs explicit wiring for settings, logging, future providers,
repositories, and application services. It must preserve clean dependency
boundaries, support test substitutions, and avoid global state.

## Decision

Use a lightweight custom `Container` in `app.core.container`. The constructor
receives `Settings` and configures logging. Dependencies will be assembled
explicitly through factory methods as their interfaces and implementations are
introduced. The container exposes asynchronous `start` and `stop` lifecycle
hooks and is not a global singleton.

## Consequences

Positive:

* Wiring is visible, typed, and easy to review.
* Tests can construct containers with explicit settings and substitutes.
* Domain and application code remain independent of a DI framework.

Negative:

* Composition code must be maintained manually as the dependency graph grows.
* The container must remain small to avoid becoming a service locator.

