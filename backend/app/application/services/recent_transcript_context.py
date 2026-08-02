"""Session-scoped, bounded finalized transcript context for reply coaching."""

from collections import deque
from threading import RLock

from app.application.dto.assist_mode import ReplyContext, TranscriptSegment
from app.application.dto.assist_mode.reply_context import (
    MAX_CONTEXT_TEXT_LENGTH,
    MAX_PREVIOUS_SEGMENTS,
)
from app.application.exceptions import ApplicationValidationError


class RecentTranscriptContext:
    """Keep recent immutable transcript segments in chronological memory only."""

    def __init__(self) -> None:
        """Initialize one empty context owned by a live-transcription session."""

        self._segments: deque[TranscriptSegment] = deque()
        self._lock = RLock()

    def add(self, segment: TranscriptSegment) -> None:
        """Append one finalized segment and trim only the oldest retained entries."""

        if not isinstance(segment, TranscriptSegment):
            raise ApplicationValidationError("Transcript segment is invalid.")
        if len(segment.text) > MAX_CONTEXT_TEXT_LENGTH:
            raise ApplicationValidationError(
                "Newest transcript segment exceeds the context length limit."
            )

        with self._lock:
            self._segments.append(segment)
            self._trim_oldest()

    def clear(self) -> None:
        """Discard all session-local context without touching persisted transcripts."""

        with self._lock:
            self._segments.clear()

    def latest(self) -> TranscriptSegment | None:
        """Return the newest retained segment, if one has been accepted."""

        with self._lock:
            return self._segments[-1] if self._segments else None

    def build_reply_context(self) -> ReplyContext | None:
        """Create the existing bounded DTO from a chronological immutable snapshot."""

        with self._lock:
            if not self._segments:
                return None
            segments = tuple(self._segments)
        return ReplyContext(
            newest_finalized_segment=segments[-1],
            previous_finalized_segments=segments[:-1],
        )

    def segment_count(self) -> int:
        """Return the number of retained finalized segments."""

        with self._lock:
            return len(self._segments)

    def is_empty(self) -> bool:
        """Report whether this session has retained any finalized segment."""

        with self._lock:
            return not self._segments

    def _trim_oldest(self) -> None:
        """Enforce total and character capacity while preserving the newest entry."""

        while len(self._segments) > MAX_PREVIOUS_SEGMENTS + 1:
            self._segments.popleft()
        while (
            len(self._segments) > 1
            and self._combined_text_length() > MAX_CONTEXT_TEXT_LENGTH
        ):
            self._segments.popleft()

    def _combined_text_length(self) -> int:
        """Measure chronological context text exactly as ReplyContext will render it."""

        return len("\n".join(segment.text for segment in self._segments))
