"""Retention policies for local recording metadata."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.application.exceptions import ApplicationValidationError


class RecordingRetentionPolicy(StrEnum):
    """Supported automatic deletion policies."""

    ONE_DAY = "one_day"
    SEVEN_DAYS = "seven_days"
    THIRTY_DAYS = "thirty_days"
    MANUAL = "manual"

    def expires_at(self, created_at: datetime) -> datetime | None:
        """Derive a deterministic UTC expiry from a creation timestamp."""

        if created_at.tzinfo is None or created_at.utcoffset() != timedelta(0):
            raise ApplicationValidationError("Recording timestamps must use UTC.")
        if self is self.MANUAL:
            return None
        days = {
            self.ONE_DAY: 1,
            self.SEVEN_DAYS: 7,
            self.THIRTY_DAYS: 30,
        }[self]
        return (created_at + timedelta(days=days)).astimezone(UTC)
