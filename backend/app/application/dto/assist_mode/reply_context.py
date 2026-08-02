"""Bounded finalized transcript context for reply coaching."""

from dataclasses import dataclass

from app.application.dto.assist_mode.transcript_segment import TranscriptSegment
from app.application.exceptions import ApplicationValidationError

MAX_PREVIOUS_SEGMENTS = 6
MAX_CONTEXT_TEXT_LENGTH = 1_200


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplyContext:
    """Newest finalized segment with a bounded, oldest-first-trimmed history."""

    newest_finalized_segment: TranscriptSegment
    previous_finalized_segments: tuple[TranscriptSegment, ...] = ()

    def __post_init__(self) -> None:
        """Keep only recent finalized context within the V1 character budget."""

        if not isinstance(self.newest_finalized_segment, TranscriptSegment):
            raise ApplicationValidationError("Newest transcript segment is invalid.")
        if not all(
            isinstance(segment, TranscriptSegment)
            for segment in self.previous_finalized_segments
        ):
            raise ApplicationValidationError(
                "Previous transcript segments are invalid."
            )

        segments = list(self.previous_finalized_segments[-MAX_PREVIOUS_SEGMENTS:])
        while (
            segments and self._combined_text_length(segments) > MAX_CONTEXT_TEXT_LENGTH
        ):
            segments.pop(0)
        object.__setattr__(self, "previous_finalized_segments", tuple(segments))

        if self._combined_text_length(segments) > MAX_CONTEXT_TEXT_LENGTH:
            trimmed_text = self.newest_finalized_segment.text[
                -MAX_CONTEXT_TEXT_LENGTH:
            ].strip()
            object.__setattr__(
                self,
                "newest_finalized_segment",
                TranscriptSegment(
                    transcript_id=self.newest_finalized_segment.transcript_id,
                    meeting_id=self.newest_finalized_segment.meeting_id,
                    text=trimmed_text,
                    timestamp=self.newest_finalized_segment.timestamp,
                    source=self.newest_finalized_segment.source,
                    speaker=self.newest_finalized_segment.speaker,
                ),
            )

    @property
    def conversation_text(self) -> str:
        """Return bounded context in chronological order for the provider request."""

        return "\n".join(
            segment.text
            for segment in (
                *self.previous_finalized_segments,
                self.newest_finalized_segment,
            )
        )

    def _combined_text_length(self, previous_segments: list[TranscriptSegment]) -> int:
        return len(
            "\n".join(
                segment.text
                for segment in (*previous_segments, self.newest_finalized_segment)
            )
        )
