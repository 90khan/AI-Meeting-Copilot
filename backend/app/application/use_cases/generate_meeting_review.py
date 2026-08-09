"""Generate a versioned hierarchical structured review for an ended Meeting."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.dto.meeting_review import (
    BatchReviewResult,
    GenerateMeetingReviewCommand,
    GenerateMeetingReviewResult,
    MeetingDetail,
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
    MeetingReviewGenerationStage,
)
from app.application.exceptions import ProviderError
from app.application.interfaces import UnitOfWorkFactory
from app.application.interfaces.ai import MeetingReviewGenerationProvider
from app.application.services import MeetingReviewBatcher, MeetingReviewMerger
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId, MeetingStatus

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
        utc_clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        provider_name: str,
        model_name: str,
        prompt_version: str,
        schema_version: int = 1,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._meeting_review_generation_provider = meeting_review_generation_provider
        self._meeting_review_batcher = meeting_review_batcher
        self._meeting_review_merger = meeting_review_merger
        self._utc_clock = utc_clock
        self._uuid_factory = uuid_factory
        self._provider_name = provider_name
        self._model_name = model_name
        self._prompt_version = prompt_version
        self._schema_version = schema_version

    async def execute(
        self,
        command: GenerateMeetingReviewCommand,
    ) -> GenerateMeetingReviewResult:
        """Persist a new versioned review for the ended Meeting's full transcript."""

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
            review_artifacts = unit_of_work.meeting_review_artifacts
            latest_version = await review_artifacts.get_latest_version(
                command.meeting_id, "interview_review"
            )
            processing = self._new_processing_artifact(
                meeting_id=command.meeting_id,
                source_transcript_count=len(detail.transcript),
                version=latest_version + 1,
            )

        await self._persist_new_artifact(processing)

        try:
            content = await self._generate_content(detail)
        except ProviderError:
            await self._persist_terminal_artifact(
                artifact_id=processing.artifact_id,
                status=MeetingReviewArtifactStatus.FAILED,
                content=None,
                failure_code="review_provider_failed",
            )
            raise
        except asyncio.CancelledError:
            await self._best_effort_persist_cancelled(processing.artifact_id)
            raise

        artifact = await self._persist_terminal_artifact(
            artifact_id=processing.artifact_id,
            status=MeetingReviewArtifactStatus.COMPLETED,
            content=content,
            failure_code=None,
        )
        return GenerateMeetingReviewResult(artifact=artifact, reused_existing=False)

    def _new_processing_artifact(
        self,
        *,
        meeting_id: MeetingId,
        source_transcript_count: int,
        version: int,
    ) -> MeetingReviewArtifact:
        """Build the first lifecycle state without claiming persisted reuse."""

        return MeetingReviewArtifact(
            artifact_id=self._uuid_factory(),
            meeting_id=meeting_id,
            version=version,
            review_type="interview_review",
            status=MeetingReviewArtifactStatus.PROCESSING,
            created_at=self._utc_clock(),
            completed_at=None,
            source_transcript_count=source_transcript_count,
            content=None,
            provider_name=self._provider_name,
            model_name=self._model_name,
            prompt_version=self._prompt_version,
            schema_version=self._schema_version,
            failure_code=None,
        )

    async def _persist_new_artifact(self, artifact: MeetingReviewArtifact) -> None:
        """Persist processing in an independent short transaction."""

        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.meeting_review_artifacts.save(artifact)
            await unit_of_work.commit()

    async def _persist_terminal_artifact(
        self,
        *,
        artifact_id: UUID,
        status: MeetingReviewArtifactStatus,
        content: MeetingReviewContent | None,
        failure_code: str | None,
    ) -> MeetingReviewArtifact:
        """Reload and replace the processing artifact in a fresh transaction."""

        async with self._unit_of_work_factory() as unit_of_work:
            processing = await unit_of_work.meeting_review_artifacts.get_by_id(
                artifact_id
            )
            if processing is None:
                raise LookupError("Meeting review artifact not found")
            artifact = replace(
                processing,
                status=status,
                completed_at=(
                    self._utc_clock()
                    if status is MeetingReviewArtifactStatus.COMPLETED
                    else None
                ),
                content=content,
                failure_code=failure_code,
            )
            await unit_of_work.meeting_review_artifacts.save(artifact)
            await unit_of_work.commit()
            return artifact

    async def _best_effort_persist_cancelled(self, artifact_id: UUID) -> None:
        """Persist cancellation without allowing persistence failure to mask it."""

        try:
            await self._persist_terminal_artifact(
                artifact_id=artifact_id,
                status=MeetingReviewArtifactStatus.CANCELLED,
                content=None,
                failure_code="review_cancelled",
            )
        except Exception:
            return

    async def _generate_content(self, detail: MeetingDetail) -> MeetingReviewContent:
        """Run the existing sequential batch and final reduce workflow unchanged."""

        if not detail.transcript:
            return MeetingReviewContent(
                summary=_EMPTY_REVIEW_SUMMARY,
                key_decisions=(),
                action_items=(),
                open_questions=(),
                technical_questions=(),
                technical_terms=(),
                feedback=None,
            )

        batch_results: list[BatchReviewResult] = []
        for batch in self._meeting_review_batcher.build_batches(detail.transcript):
            content = await self._meeting_review_generation_provider.generate_review(
                MeetingReviewGenerationRequest(
                    meeting_id=detail.meeting_id,
                    stage=MeetingReviewGenerationStage.BATCH,
                    batch=batch,
                    intermediate_content=None,
                )
            )
            batch_results.append(
                BatchReviewResult(batch_index=batch.batch_index, content=content)
            )
        intermediate_content = self._meeting_review_merger.merge(tuple(batch_results))
        return await self._meeting_review_generation_provider.generate_review(
            MeetingReviewGenerationRequest(
                meeting_id=detail.meeting_id,
                stage=MeetingReviewGenerationStage.REDUCE,
                batch=None,
                intermediate_content=intermediate_content,
            )
        )
