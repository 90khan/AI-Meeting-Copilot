"""Immutable, privacy-safe recording cleanup result DTOs."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.application.exceptions import ApplicationValidationError

_ALLOWED_MESSAGES = frozenset(
    {
        "Recording deleted.",
        "Recording skipped.",
        "Recording cleanup failed.",
        "Recording storage was missing.",
        "Recording is protected.",
        "Recording is not expired.",
        "Recording uses manual retention.",
    }
)


class RecordingCleanupOutcome(StrEnum):
    DELETED = "deleted"
    SKIPPED = "skipped"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingCleanupItemResult:
    """One privacy-safe cleanup outcome for a recording."""

    recording_id: UUID
    outcome: RecordingCleanupOutcome
    message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.recording_id, UUID):
            raise ApplicationValidationError("Recording cleanup item is invalid.")
        if not isinstance(self.outcome, RecordingCleanupOutcome):
            raise ApplicationValidationError("Recording cleanup item is invalid.")
        if self.message is not None:
            message = self.message.strip()
            if len(message) > 120 or message not in _ALLOWED_MESSAGES:
                raise ApplicationValidationError("Recording cleanup item is invalid.")
            object.__setattr__(self, "message", message)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingCleanupResult:
    """Consistent aggregate counts for ordered cleanup results."""

    checked_count: int
    deleted_count: int
    skipped_count: int
    failed_count: int
    missing_count: int
    items: tuple[RecordingCleanupItemResult, ...]

    def __post_init__(self) -> None:
        counts = (
            self.checked_count,
            self.deleted_count,
            self.skipped_count,
            self.failed_count,
            self.missing_count,
        )
        if not all(isinstance(count, int) and count >= 0 for count in counts):
            raise ApplicationValidationError("Recording cleanup result is invalid.")
        if not isinstance(self.items, tuple):
            raise ApplicationValidationError("Recording cleanup result is invalid.")
        if not all(isinstance(item, RecordingCleanupItemResult) for item in self.items):
            raise ApplicationValidationError("Recording cleanup result is invalid.")
        if self.checked_count != len(self.items) or self._counts(self.items) != (
            self.deleted_count,
            self.skipped_count,
            self.failed_count,
            self.missing_count,
        ):
            raise ApplicationValidationError("Recording cleanup result is invalid.")

    @classmethod
    def from_items(
        cls, items: Iterable[RecordingCleanupItemResult]
    ) -> "RecordingCleanupResult":
        """Build counts from any ordered iterable of item results."""

        collected = tuple(items)
        deleted, skipped, failed, missing = cls._counts(collected)
        return cls(
            checked_count=len(collected),
            deleted_count=deleted,
            skipped_count=skipped,
            failed_count=failed,
            missing_count=missing,
            items=collected,
        )

    @staticmethod
    def _counts(
        items: tuple[RecordingCleanupItemResult, ...],
    ) -> tuple[int, int, int, int]:
        return (
            sum(item.outcome is RecordingCleanupOutcome.DELETED for item in items),
            sum(item.outcome is RecordingCleanupOutcome.SKIPPED for item in items),
            sum(item.outcome is RecordingCleanupOutcome.FAILED for item in items),
            sum(item.outcome is RecordingCleanupOutcome.MISSING for item in items),
        )
