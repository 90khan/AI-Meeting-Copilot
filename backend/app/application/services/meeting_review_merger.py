"""Deterministically merge already-generated Meeting review batch results."""

from collections.abc import Iterable

from app.application.dto.meeting_review import (
    MeetingReviewContent,
    ReviewFeedback,
)
from app.application.dto.meeting_review.review_batch import BatchReviewResult
from app.application.exceptions import ApplicationValidationError


class MeetingReviewMerger:
    """Merge ordered batch results using exact-value, first-seen deduplication only."""

    def merge(
        self,
        batches: tuple[BatchReviewResult, ...],
    ) -> MeetingReviewContent:
        """Return one deterministic intermediate content from contiguous batches."""

        ordered_batches = self._validate_and_order(batches)
        contents = tuple(batch.content for batch in ordered_batches)
        return MeetingReviewContent(
            summary="\n\n".join(content.summary for content in contents),
            key_decisions=self._deduplicate(
                item for content in contents for item in content.key_decisions
            ),
            action_items=self._deduplicate(
                item for content in contents for item in content.action_items
            ),
            open_questions=self._deduplicate(
                item for content in contents for item in content.open_questions
            ),
            technical_questions=self._deduplicate(
                item for content in contents for item in content.technical_questions
            ),
            technical_terms=self._deduplicate(
                item for content in contents for item in content.technical_terms
            ),
            feedback=self._merge_feedback(contents),
        )

    @staticmethod
    def _validate_and_order(
        batches: tuple[BatchReviewResult, ...],
    ) -> tuple[BatchReviewResult, ...]:
        if (
            not isinstance(batches, tuple)
            or not batches
            or not all(isinstance(batch, BatchReviewResult) for batch in batches)
        ):
            raise ApplicationValidationError(
                "Meeting review batch results are invalid."
            )
        ordered = tuple(sorted(batches, key=lambda batch: batch.batch_index))
        if [batch.batch_index for batch in ordered] != list(range(len(ordered))):
            raise ApplicationValidationError(
                "Meeting review batch results are invalid."
            )
        return ordered

    @staticmethod
    def _deduplicate[T](items: Iterable[T]) -> tuple[T, ...]:
        retained: list[T] = []
        for item in items:
            if item not in retained:
                retained.append(item)
        return tuple(retained)

    @classmethod
    def _merge_feedback(
        cls,
        contents: tuple[MeetingReviewContent, ...],
    ) -> ReviewFeedback | None:
        feedback_items = tuple(
            content.feedback for content in contents if content.feedback is not None
        )
        if not feedback_items:
            return None
        return ReviewFeedback(
            strengths=cls._deduplicate(
                strength
                for feedback in feedback_items
                for strength in feedback.strengths
            ),
            improvement_areas=cls._deduplicate(
                area
                for feedback in feedback_items
                for area in feedback.improvement_areas
            ),
            overall_feedback="\n\n".join(
                feedback.overall_feedback for feedback in feedback_items
            ),
        )
