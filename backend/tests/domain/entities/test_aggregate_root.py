"""Tests for the base aggregate-root abstraction."""

from app.domain.entities import AggregateRoot
from app.domain.events import DomainEvent


class ExampleAggregate(AggregateRoot[str]):
    """Concrete aggregate used to verify base behavior."""


class ExampleEvent(DomainEvent[str]):
    """Concrete event used to verify aggregate behavior."""

    EVENT_TYPE = "example.recorded"


def test_pull_domain_events_preserves_recording_order() -> None:
    """Pending events are returned in the order in which they were recorded."""

    aggregate = ExampleAggregate("aggregate-1")
    first_event = ExampleEvent(aggregate_id="aggregate-1")
    second_event = ExampleEvent(aggregate_id="aggregate-1")

    aggregate.record_event(first_event)
    aggregate.record_event(second_event)

    assert aggregate.pull_domain_events() == (first_event, second_event)


def test_pull_domain_events_returns_a_tuple() -> None:
    """Pending events are exposed as an immutable tuple."""

    aggregate = ExampleAggregate("aggregate-1")
    aggregate.record_event(ExampleEvent(aggregate_id="aggregate-1"))

    assert isinstance(aggregate.pull_domain_events(), tuple)


def test_pull_domain_events_clears_pending_events() -> None:
    """Pulling events clears the aggregate's pending event collection."""

    aggregate = ExampleAggregate("aggregate-1")
    aggregate.record_event(ExampleEvent(aggregate_id="aggregate-1"))

    aggregate.pull_domain_events()

    assert aggregate.pull_domain_events() == ()
