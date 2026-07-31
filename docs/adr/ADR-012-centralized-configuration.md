# ADR-012: Centralize Configuration with Pydantic Settings

## Status

Accepted

## Context

Environment-specific values, including log level and future provider settings,
must be validated consistently without exposing secrets or scattering direct
environment access across modules.

## Decision

Use a typed Pydantic v2 `Settings` class with `SettingsConfigDict`. Settings use
the `AI_MEETING_COPILOT_` prefix, load an optional repository-root `.env` file,
ignore unknown values, and are immutable after validation. `get_settings()`
uses a one-entry cache for process-wide settings reuse; this does not create a
global application container.

## Consequences

Positive:

* Configuration is validated, typed, and centralized.
* Modules receive settings through dependency wiring instead of reading the
  environment directly.
* Local development can use `.env` without committing secrets.

Negative:

* New configuration values require deliberate schema changes and documentation.
* Tests that change environment values must clear or bypass the settings cache.

