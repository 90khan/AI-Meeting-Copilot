"""Base abstraction for domain aggregate roots."""

from app.domain.entities.entity import Entity


class AggregateRoot[IdT](Entity[IdT]):
    """An entity that records domain events pending application handling."""

    def __init__(self, entity_id: IdT) -> None:
        """Create an aggregate root with no pending domain events."""

        super().__init__(entity_id)
        self._pending_domain_events: list[object] = []

    def record_event(self, event: object) -> None:
        """Record a pending domain event for later application handling."""

        self._pending_domain_events.append(event)

    def pull_domain_events(self) -> tuple[object, ...]:
        """Return pending events in order and clear the pending collection."""

        events = tuple(self._pending_domain_events)
        self._pending_domain_events.clear()
        return events
