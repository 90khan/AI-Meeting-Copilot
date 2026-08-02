"""Result returned after adding a transcript entry."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class AddTranscriptResult:
    """Identify the transcript entry created by the Meeting aggregate."""

    transcript_id: UUID
