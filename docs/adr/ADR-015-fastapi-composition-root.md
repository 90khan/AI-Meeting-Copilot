# ADR-015: Use FastAPI as the Application Composition Root

## Status

Accepted

## Context

The backend requires deterministic startup and shutdown while preserving a thin
HTTP layer. Future infrastructure resources must be owned by an application
instance rather than process-wide globals.

## Decision

Use `create_app(settings: Settings | None = None)` to create each FastAPI
instance. It creates a `Container`, stores it on `app.state.container`, and uses
an asynchronous lifespan to await `container.start()` before serving requests
and `container.stop()` during shutdown. Routes access narrow dependencies in a
future API dependency layer. The initial bootstrap registers only `GET /health`.

## Consequences

Positive:

* Application instances are isolated and straightforward to test.
* Lifecycle ownership is explicit and supports future managed resources.
* Routes remain transport adapters rather than composition code.

Negative:

* FastAPI-specific state access must stay confined to the API/composition edge.
* New resource lifecycles must be registered deliberately with the container.

