"""Deterministically divide complete transcript entries into review batches."""

from app.application.dto.meeting_review import TranscriptReadItem
from app.application.dto.meeting_review.review_batch import TranscriptReviewBatch
from app.application.exceptions import ApplicationValidationError


class MeetingReviewBatcher:
    """Build bounded batches while retaining whole transcript entries and overlap."""

    def __init__(
        self,
        *,
        max_batch_characters: int,
        overlap_segments: int = 1,
    ) -> None:
        if (
            type(max_batch_characters) is not int
            or max_batch_characters <= 0
            or type(overlap_segments) is not int
            or overlap_segments < 0
        ):
            raise ApplicationValidationError(
                "Meeting review batch configuration is invalid."
            )
        self._max_batch_characters = max_batch_characters
        self._overlap_segments = overlap_segments

    def build_batches(
        self,
        transcript: tuple[TranscriptReadItem, ...],
    ) -> tuple[TranscriptReviewBatch, ...]:
        """Return deterministic batches without truncating or splitting any entry."""

        if not isinstance(transcript, tuple) or not all(
            isinstance(entry, TranscriptReadItem) for entry in transcript
        ):
            raise ApplicationValidationError("Meeting review transcript is invalid.")
        if not transcript:
            return ()

        batches: list[TranscriptReviewBatch] = []
        start_index = 0
        while start_index < len(transcript):
            end_exclusive = self._next_end_index(transcript, start_index)
            entries = transcript[start_index:end_exclusive]
            batches.append(
                TranscriptReviewBatch(
                    batch_index=len(batches),
                    start_transcript_index=start_index,
                    end_transcript_index=end_exclusive - 1,
                    transcript_ids=tuple(entry.transcript_id for entry in entries),
                    texts=tuple(entry.text for entry in entries),
                    total_characters=sum(len(entry.text) for entry in entries),
                )
            )
            if end_exclusive == len(transcript):
                break
            start_index = max(
                start_index + 1,
                end_exclusive - self._overlap_segments,
            )

        return tuple(batches)

    def _next_end_index(
        self,
        transcript: tuple[TranscriptReadItem, ...],
        start_index: int,
    ) -> int:
        """Return the first excluded index, allowing one oversized entry alone."""

        total_characters = 0
        end_index = start_index
        while end_index < len(transcript):
            text_length = len(transcript[end_index].text)
            if end_index > start_index and (
                total_characters + text_length > self._max_batch_characters
            ):
                break
            total_characters += text_length
            end_index += 1
            if total_characters >= self._max_batch_characters:
                break
        return end_index
