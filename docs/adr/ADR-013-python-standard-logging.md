# ADR-013: Use Python Standard-Library Logging

## Status

Accepted

## Context

The foundation needs consistent console logging with configurable levels while
keeping dependencies and operational complexity low. Meeting content and secrets
must not be exposed through logs.

## Decision

Use Python's built-in `logging` module. `setup_logging(settings)` configures one
named console handler with a single text formatter and is idempotent. Log level
is read from centralized settings. Modules obtain module-qualified loggers using
`get_logger(name)`.

Structured JSON output, file logging, rotation, correlation IDs, FastAPI
middleware integration, and provider-specific logging are deferred.

## Consequences

Positive:

* No additional logging dependency is required.
* Console logging is predictable and safe to initialize repeatedly.
* Later observability features can extend a stable logging boundary.

Negative:

* Initial logs have limited machine-readable context.
* Rich request and provider telemetry requires future work.

