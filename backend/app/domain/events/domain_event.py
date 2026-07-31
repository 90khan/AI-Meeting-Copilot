"""Base abstraction for immutable domain events."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID, uuid4

from app.domain.exceptions import ValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent[AggregateIdT]:
    """An immutable fact emitted by a domain aggregate."""

    aggregate_id: AggregateIdT
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_version: int = 1

    EVENT_TYPE: ClassVar[str] = ""

    def __post_init__(self) -> None:
        """Validate required event metadata."""

        if not self.EVENT_TYPE.strip():
            raise ValidationError("EVENT_TYPE must not be empty.")
        if self.event_version < 1:
            raise ValidationError("event_version must be at least 1.")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValidationError("occurred_at must be timezone-aware.")
        if self.occurred_at.utcoffset() != timedelta(0):
            raise ValidationError("occurred_at must use UTC.")

    @property
    def event_type(self) -> str:
        """Return the stable event type declared by the concrete event."""

        return self.EVENT_TYPE
