"""Tests for the immutable domain event abstraction."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.events import DomainEvent
from app.domain.exceptions import ValidationError


class ExampleEvent(DomainEvent[str]):
    """Concrete event used to verify base behavior."""

    EVENT_TYPE = "example.created"


class EmptyTypeEvent(DomainEvent[str]):
    """Concrete event with an invalid empty event type."""

    EVENT_TYPE = ""


def test_domain_event_generates_a_uuid() -> None:
    """An event receives a UUID when one is not supplied."""

    assert isinstance(ExampleEvent(aggregate_id="aggregate-1").event_id, UUID)


def test_domain_event_preserves_an_explicit_uuid() -> None:
    """An explicitly supplied event identity is retained."""

    event_id = uuid4()

    event = ExampleEvent(aggregate_id="aggregate-1", event_id=event_id)

    assert event.event_id == event_id


def test_domain_event_uses_a_timezone_aware_utc_timestamp() -> None:
    """The default occurrence time is timezone-aware and uses UTC."""

    occurred_at = ExampleEvent(aggregate_id="aggregate-1").occurred_at

    assert occurred_at.tzinfo is UTC


def test_domain_event_rejects_a_naive_timestamp() -> None:
    """A timestamp without timezone information is invalid."""

    with pytest.raises(ValidationError):
        ExampleEvent(aggregate_id="aggregate-1", occurred_at=datetime(2026, 1, 1))


def test_domain_event_uses_version_one_by_default() -> None:
    """The initial event schema version is one."""

    assert ExampleEvent(aggregate_id="aggregate-1").event_version == 1


def test_domain_event_rejects_an_invalid_version() -> None:
    """Event schema versions must be positive."""

    with pytest.raises(ValidationError):
        ExampleEvent(aggregate_id="aggregate-1", event_version=0)


def test_domain_event_exposes_its_stable_type() -> None:
    """A concrete event exposes its declared stable event type."""

    assert ExampleEvent(aggregate_id="aggregate-1").event_type == "example.created"


def test_domain_event_rejects_an_empty_event_type() -> None:
    """A concrete event must declare a non-empty event type."""

    with pytest.raises(ValidationError):
        EmptyTypeEvent(aggregate_id="aggregate-1")


def test_domain_event_is_immutable() -> None:
    """Event metadata cannot be mutated after construction."""

    event = ExampleEvent(aggregate_id="aggregate-1")

    with pytest.raises(FrozenInstanceError):
        event.event_version = 2
