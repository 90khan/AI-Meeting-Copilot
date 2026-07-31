"""Immutable transcript entries owned by a Meeting aggregate."""

from datetime import datetime, timedelta
from uuid import UUID

from app.domain.entities.entity import Entity
from app.domain.exceptions import ValidationError


class TranscriptEntry(Entity[UUID]):
    """An immutable, identity-bearing entry in a Meeting transcript."""

    __slots__ = ("_is_initialized", "_speaker", "_text", "_timestamp")

    def __init__(
        self,
        entry_id: UUID,
        speaker: str,
        text: str,
        timestamp: datetime,
    ) -> None:
        """Create an immutable transcript entry with validated content."""

        self._is_initialized = False
        super().__init__(entry_id)
        self._speaker = self._validate_text(speaker, "speaker")
        self._text = self._validate_text(text, "text")
        self._timestamp = self._validate_timestamp(timestamp)
        self._is_initialized = True

    @property
    def speaker(self) -> str:
        """Return the identified speaker."""

        return self._speaker

    @property
    def text(self) -> str:
        """Return the transcript text."""

        return self._text

    @property
    def timestamp(self) -> datetime:
        """Return the UTC timestamp of the entry."""

        return self._timestamp

    def __setattr__(self, name: str, value: object) -> None:
        """Prevent changes after construction."""

        if getattr(self, "_is_initialized", False):
            raise AttributeError("TranscriptEntry instances are immutable.")

        super().__setattr__(name, value)

    @staticmethod
    def _validate_text(value: str, field_name: str) -> str:
        """Validate and return a non-blank transcript text field."""

        if not value.strip():
            raise ValidationError(f"Transcript {field_name} must not be blank.")

        return value

    @staticmethod
    def _validate_timestamp(timestamp: datetime) -> datetime:
        """Validate and return a timezone-aware UTC timestamp."""

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValidationError("Transcript timestamp must be timezone-aware.")
        if timestamp.utcoffset() != timedelta(0):
            raise ValidationError("Transcript timestamp must use UTC.")

        return timestamp
