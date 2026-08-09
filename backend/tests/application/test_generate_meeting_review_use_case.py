"""Tests for versioned hierarchical Meeting review generation."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto import AudioSource
from app.application.dto.meeting_review import (
    GenerateMeetingReviewCommand,
    MeetingDetail,
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
    MeetingReviewGenerationStage,
    TranscriptReadItem,
)
from app.application.exceptions import (
    ApplicationValidationError,
    ProviderUnavailableError,
)
from app.application.services import MeetingReviewBatcher, MeetingReviewMerger
from app.application.use_cases import GenerateMeetingReviewUseCase
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId, MeetingStatus

_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000101"))


def _transcript_entry(index: int, text: str | None = None) -> TranscriptReadItem:
    return TranscriptReadItem(
        transcript_id=UUID(int=index + 1),
        text=text or f"Transcript entry {index}.",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        speaker="Unknown",
        source=AudioSource.MIXED,
    )


def _detail(
    *,
    status: MeetingStatus = MeetingStatus.ENDED,
    transcript: tuple[TranscriptReadItem, ...] = (),
) -> MeetingDetail:
    return MeetingDetail(
        meeting_id=_MEETING_ID,
        title="Interview",
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, tzinfo=UTC),
        transcript=transcript,
        recording_available=False,
        recording_state=None,
        recording_duration_seconds=None,
        audio_expires_at=None,
        audio_protected=False,
        audio_has_gaps=False,
    )


def _content(summary: str) -> MeetingReviewContent:
    return MeetingReviewContent(
        summary=summary,
        key_decisions=(),
        action_items=(),
        open_questions=(),
        technical_questions=(),
        technical_terms=(),
        feedback=None,
    )


class _Reviews:
    def __init__(self, detail: MeetingDetail | None) -> None:
        self._detail = detail

    async def get_meeting_detail(self, meeting_id: MeetingId) -> MeetingDetail | None:
        assert meeting_id == _MEETING_ID
        return self._detail


class _Artifacts:
    def __init__(self, artifacts: tuple[MeetingReviewArtifact, ...] = ()) -> None:
        self._by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        self.saved: list[MeetingReviewArtifact] = []
        self.fail_terminal_save = False

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> int:
        versions = [
            artifact.version
            for artifact in self._by_id.values()
            if artifact.meeting_id == meeting_id and artifact.review_type == review_type
        ]
        return max(versions, default=0)

    async def get_by_id(self, artifact_id: UUID) -> MeetingReviewArtifact | None:
        return self._by_id.get(artifact_id)

    async def save(self, artifact: MeetingReviewArtifact) -> None:
        if (
            self.fail_terminal_save
            and artifact.status is not MeetingReviewArtifactStatus.PROCESSING
        ):
            raise RuntimeError("persistence failure")
        self._by_id[artifact.artifact_id] = artifact
        self.saved.append(artifact)


class _UnitOfWork:
    def __init__(self, factory: "_UnitOfWorkFactory") -> None:
        self._factory = factory
        self.meeting_reviews = _Reviews(factory.detail)
        self.meeting_review_artifacts = factory.artifacts
        self.commit_calls = 0
        self.exited = False

    async def __aenter__(self) -> "_UnitOfWork":
        self._factory.active_count += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self._factory.active_count -= 1
        self.exited = True

    async def commit(self) -> None:
        self.commit_calls += 1


class _UnitOfWorkFactory:
    def __init__(
        self,
        detail: MeetingDetail | None,
        *,
        artifacts: tuple[MeetingReviewArtifact, ...] = (),
    ) -> None:
        self.detail = detail
        self.artifacts = _Artifacts(artifacts)
        self.created: list[_UnitOfWork] = []
        self.active_count = 0
        self.uuid_factory = _UuidFactory()

    def __call__(self) -> _UnitOfWork:
        unit_of_work = _UnitOfWork(self)
        self.created.append(unit_of_work)
        return unit_of_work


class _Provider:
    def __init__(
        self,
        factory: _UnitOfWorkFactory,
        *,
        batch_error: BaseException | None = None,
        reduce_error: BaseException | None = None,
    ) -> None:
        self._factory = factory
        self.requests: list[MeetingReviewGenerationRequest] = []
        self.active_counts: list[int] = []
        self._batch_error = batch_error
        self._reduce_error = reduce_error

    async def generate_review(
        self,
        request: MeetingReviewGenerationRequest,
    ) -> MeetingReviewContent:
        self.requests.append(request)
        self.active_counts.append(self._factory.active_count)
        if request.stage is MeetingReviewGenerationStage.BATCH:
            if self._batch_error is not None:
                raise self._batch_error
            assert request.batch is not None
            return _content(f"Batch {request.batch.batch_index}.")
        if self._reduce_error is not None:
            raise self._reduce_error
        return _content("Final review.")


class _UuidFactory:
    def __init__(self) -> None:
        self._next = 500

    def __call__(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def _use_case(
    factory: _UnitOfWorkFactory,
    provider: _Provider,
    *,
    max_batch_characters: int = 100,
) -> GenerateMeetingReviewUseCase:
    return GenerateMeetingReviewUseCase(
        unit_of_work_factory=factory,
        meeting_review_generation_provider=provider,
        meeting_review_batcher=MeetingReviewBatcher(
            max_batch_characters=max_batch_characters
        ),
        meeting_review_merger=MeetingReviewMerger(),
        utc_clock=lambda: datetime(2026, 1, 2, tzinfo=UTC),
        uuid_factory=factory.uuid_factory,
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_review_v1",
    )


def _command(*, force_regenerate: bool = False) -> GenerateMeetingReviewCommand:
    return GenerateMeetingReviewCommand(
        meeting_id=_MEETING_ID,
        force_regenerate=force_regenerate,
    )


def test_missing_meeting_raises_without_provider_or_write() -> None:
    factory = _UnitOfWorkFactory(None)
    provider = _Provider(factory)

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    assert provider.requests == []
    assert factory.artifacts.saved == []
    assert factory.created[0].commit_calls == 0


@pytest.mark.parametrize("status", [MeetingStatus.DRAFT, MeetingStatus.ACTIVE])
def test_only_ended_meeting_can_generate_a_review(status: MeetingStatus) -> None:
    factory = _UnitOfWorkFactory(_detail(status=status))
    provider = _Provider(factory)

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    assert provider.requests == []
    assert factory.artifacts.saved == []


def test_processing_then_completed_preserves_identity_and_transactions() -> None:
    factory = _UnitOfWorkFactory(_detail(transcript=(_transcript_entry(0),)))
    provider = _Provider(factory)

    result = asyncio.run(_use_case(factory, provider).execute(_command()))

    processing, completed = factory.artifacts.saved
    assert processing.status is MeetingReviewArtifactStatus.PROCESSING
    assert completed.status is MeetingReviewArtifactStatus.COMPLETED
    assert completed.content == _content("Final review.")
    assert completed.artifact_id == processing.artifact_id
    assert completed.version == processing.version == 1
    assert completed.meeting_id == processing.meeting_id
    assert completed.review_type == processing.review_type == "interview_review"
    assert completed.created_at == processing.created_at
    assert completed.source_transcript_count == processing.source_transcript_count == 1
    assert completed.provider_name == processing.provider_name == "ollama"
    assert completed.model_name == processing.model_name == "qwen2.5:3b"
    assert completed.prompt_version == processing.prompt_version == "meeting_review_v1"
    assert completed.schema_version == processing.schema_version == 1
    assert result.artifact == completed
    assert result.reused_existing is False
    assert [unit.commit_calls for unit in factory.created] == [0, 1, 1]
    assert provider.active_counts == [0, 0]


def test_completed_artifact_is_not_reused_by_count_and_creates_next_version() -> None:
    prior = MeetingReviewArtifact(
        artifact_id=UUID(int=99),
        meeting_id=_MEETING_ID,
        version=1,
        review_type="interview_review",
        status=MeetingReviewArtifactStatus.COMPLETED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_transcript_count=1,
        content=_content("Prior review."),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_review_v1",
        schema_version=1,
        failure_code=None,
    )
    factory = _UnitOfWorkFactory(
        _detail(transcript=(_transcript_entry(0),)),
        artifacts=(prior,),
    )
    provider = _Provider(factory)

    result = asyncio.run(_use_case(factory, provider).execute(_command()))

    assert result.reused_existing is False
    assert result.artifact.version == 2
    assert len(provider.requests) == 2
    assert prior == factory.artifacts._by_id[prior.artifact_id]


def test_zero_transcript_follows_processing_to_completed_without_provider() -> None:
    factory = _UnitOfWorkFactory(_detail())
    provider = _Provider(factory)

    result = asyncio.run(_use_case(factory, provider).execute(_command()))

    assert [artifact.status for artifact in factory.artifacts.saved] == [
        MeetingReviewArtifactStatus.PROCESSING,
        MeetingReviewArtifactStatus.COMPLETED,
    ]
    assert result.artifact.content == _content(
        "No transcript content was available for review."
    )
    assert provider.requests == []


def test_batch_generation_is_sequential_and_final_reduce_receives_merged_content() -> (
    None
):
    transcript = tuple(_transcript_entry(index, "12345") for index in range(4))
    factory = _UnitOfWorkFactory(_detail(transcript=transcript))
    provider = _Provider(factory)

    asyncio.run(
        _use_case(factory, provider, max_batch_characters=10).execute(_command())
    )

    batch_requests = [
        request
        for request in provider.requests
        if request.stage is MeetingReviewGenerationStage.BATCH
    ]
    assert [
        request.batch.batch_index for request in batch_requests if request.batch
    ] == [0, 1, 2]
    assert provider.requests[-1].stage is MeetingReviewGenerationStage.REDUCE
    assert provider.requests[-1].batch is None
    assert provider.requests[-1].intermediate_content is not None


def test_provider_failure_persists_failed_without_partial_content() -> None:
    factory = _UnitOfWorkFactory(_detail(transcript=(_transcript_entry(0),)))
    provider = _Provider(factory, batch_error=ProviderUnavailableError())

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    processing, failed = factory.artifacts.saved
    assert failed.status is MeetingReviewArtifactStatus.FAILED
    assert failed.artifact_id == processing.artifact_id
    assert failed.version == processing.version
    assert failed.content is None
    assert failed.failure_code == "review_provider_failed"


def test_final_provider_failure_preserves_previous_artifact() -> None:
    prior = MeetingReviewArtifact(
        artifact_id=UUID(int=99),
        meeting_id=_MEETING_ID,
        version=1,
        review_type="interview_review",
        status=MeetingReviewArtifactStatus.COMPLETED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_transcript_count=1,
        content=_content("Prior review."),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_review_v1",
        schema_version=1,
        failure_code=None,
    )
    factory = _UnitOfWorkFactory(
        _detail(transcript=(_transcript_entry(0),)),
        artifacts=(prior,),
    )
    provider = _Provider(factory, reduce_error=ProviderUnavailableError())

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    assert factory.artifacts._by_id[prior.artifact_id] == prior
    assert factory.artifacts.saved[-1].version == 2
    assert factory.artifacts.saved[-1].status is MeetingReviewArtifactStatus.FAILED


def test_cancelled_generation_persists_cancellation_without_masking_it() -> None:
    factory = _UnitOfWorkFactory(_detail(transcript=(_transcript_entry(0),)))
    provider = _Provider(factory, reduce_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    assert factory.artifacts.saved[-1].status is MeetingReviewArtifactStatus.CANCELLED
    failing_factory = _UnitOfWorkFactory(_detail(transcript=(_transcript_entry(0),)))
    failing_factory.artifacts.fail_terminal_save = True
    failing_provider = _Provider(failing_factory, reduce_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_use_case(failing_factory, failing_provider).execute(_command()))


def test_failed_and_cancelled_versions_count_and_accept_force_flag() -> None:
    factory = _UnitOfWorkFactory(_detail(transcript=(_transcript_entry(0),)))
    first_provider = _Provider(factory, batch_error=ProviderUnavailableError())

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_use_case(factory, first_provider).execute(_command()))

    second_provider = _Provider(factory, reduce_error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_use_case(factory, second_provider).execute(_command()))

    third_provider = _Provider(factory)
    result = asyncio.run(
        _use_case(factory, third_provider).execute(_command(force_regenerate=True))
    )
    assert result.artifact.version == 3


def test_long_transcript_is_not_truncated_and_meeting_data_is_unchanged() -> None:
    transcript = tuple(_transcript_entry(index) for index in range(2_001))
    detail = _detail(transcript=transcript)
    factory = _UnitOfWorkFactory(detail)
    provider = _Provider(factory)

    use_case = _use_case(factory, provider, max_batch_characters=100)
    asyncio.run(use_case.execute(_command()))

    batch_requests = [
        request
        for request in provider.requests
        if request.stage is MeetingReviewGenerationStage.BATCH
    ]
    batch_ids = {
        transcript_id
        for request in batch_requests
        if request.batch is not None
        for transcript_id in request.batch.transcript_ids
    }
    assert batch_ids == {entry.transcript_id for entry in transcript}
    assert provider.requests[-1].stage is MeetingReviewGenerationStage.REDUCE
    assert detail.transcript == transcript


def test_command_requires_runtime_valid_meeting_id_and_boolean() -> None:
    with pytest.raises(ApplicationValidationError):
        GenerateMeetingReviewCommand(meeting_id="invalid")
    with pytest.raises(ApplicationValidationError):
        GenerateMeetingReviewCommand(meeting_id=_MEETING_ID, force_regenerate=1)
