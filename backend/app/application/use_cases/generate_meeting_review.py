"""Generate an in-memory hierarchical structured review for an ended Meeting."""

from app.application.dto.meeting_review import (
    BatchReviewResult,
    GenerateMeetingReviewCommand,
    GenerateMeetingReviewResult,
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
    MeetingReviewGenerationStage,
)
from app.application.interfaces import UnitOfWorkFactory
from app.application.interfaces.ai import MeetingReviewGenerationProvider
from app.application.services import MeetingReviewBatcher, MeetingReviewMerger
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingStatus

_EMPTY_REVIEW_SUMMARY = "No transcript content was available for review."


class GenerateMeetingReviewUseCase:
    """Orchestrate deterministic batch and reduce review generation in memory."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        meeting_review_generation_provider: MeetingReviewGenerationProvider,
        meeting_review_batcher: MeetingReviewBatcher,
        meeting_review_merger: MeetingReviewMerger,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._meeting_review_generation_provider = meeting_review_generation_provider
        self._meeting_review_batcher = meeting_review_batcher
        self._meeting_review_merger = meeting_review_merger

    async def execute(
        self,
        command: GenerateMeetingReviewCommand,
    ) -> GenerateMeetingReviewResult:
        """Return a new in-memory review for the ended Meeting's full transcript."""

        async with self._unit_of_work_factory() as unit_of_work:
            detail = await unit_of_work.meeting_reviews.get_meeting_detail(
                command.meeting_id
            )
        if detail is None:
            raise LookupError("Meeting not found")
        if detail.status is not MeetingStatus.ENDED:
            raise InvalidStateTransitionError(
                "Meeting must be ended before generating a review."
            )
        if not detail.transcript:
            return GenerateMeetingReviewResult(
                content=MeetingReviewContent(
                    summary=_EMPTY_REVIEW_SUMMARY,
                    key_decisions=(),
                    action_items=(),
                    open_questions=(),
                    technical_questions=(),
                    technical_terms=(),
                    feedback=None,
                ),
                reused_existing=False,
            )

        batch_results: list[BatchReviewResult] = []
        for batch in self._meeting_review_batcher.build_batches(detail.transcript):
            content = await self._meeting_review_generation_provider.generate_review(
                MeetingReviewGenerationRequest(
                    meeting_id=command.meeting_id,
                    stage=MeetingReviewGenerationStage.BATCH,
                    batch=batch,
                    intermediate_content=None,
                )
            )
            batch_results.append(
                BatchReviewResult(batch_index=batch.batch_index, content=content)
            )

        intermediate_content = self._meeting_review_merger.merge(tuple(batch_results))
        final_content = await self._meeting_review_generation_provider.generate_review(
            MeetingReviewGenerationRequest(
                meeting_id=command.meeting_id,
                stage=MeetingReviewGenerationStage.REDUCE,
                batch=None,
                intermediate_content=intermediate_content,
            )
        )
        return GenerateMeetingReviewResult(
            content=final_content,
            reused_existing=False,
        )
