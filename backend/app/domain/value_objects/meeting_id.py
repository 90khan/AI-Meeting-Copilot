"""Immutable identity value object for Meeting aggregates."""

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class MeetingId:
    """A stable, UUID-backed identity for a Meeting aggregate."""

    value: UUID

    @classmethod
    def new(cls) -> Self:
        """Create a new Meeting identity."""

        return cls(uuid4())

    def __str__(self) -> str:
        """Return the UUID in its canonical string form."""

        return str(self.value)
