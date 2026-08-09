"""Tests for in-memory hierarchical Meeting review generation."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto import AudioSource
from app.application.dto.meeting_review import (
    BatchReviewResult,
    GenerateMeetingReviewCommand,
    MeetingDetail,
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


class _UnitOfWork:
    def __init__(self, detail: MeetingDetail | None) -> None:
        self.meeting_reviews = _Reviews(detail)
        self.commit_calls = 0
        self.exited = False

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.exited = True

    async def commit(self) -> None:
        self.commit_calls += 1


class _UnitOfWorkFactory:
    def __init__(self, detail: MeetingDetail | None) -> None:
        self._detail = detail
        self.created: list[_UnitOfWork] = []

    def __call__(self) -> _UnitOfWork:
        unit_of_work = _UnitOfWork(self._detail)
        self.created.append(unit_of_work)
        return unit_of_work


class _Provider:
    def __init__(
        self,
        *,
        batch_error: BaseException | None = None,
        reduce_error: BaseException | None = None,
    ) -> None:
        self.requests: list[MeetingReviewGenerationRequest] = []
        self._batch_error = batch_error
        self._reduce_error = reduce_error

    async def generate_review(
        self,
        request: MeetingReviewGenerationRequest,
    ) -> MeetingReviewContent:
        self.requests.append(request)
        if request.stage is MeetingReviewGenerationStage.BATCH:
            if self._batch_error is not None:
                raise self._batch_error
            assert request.batch is not None
            return _content(f"Batch {request.batch.batch_index}.")
        if self._reduce_error is not None:
            raise self._reduce_error
        return _content("Final review.")


class _Merger:
    def __init__(self) -> None:
        self.called_with: tuple[BatchReviewResult, ...] | None = None
        self._delegate = MeetingReviewMerger()

    def merge(self, batches: tuple[BatchReviewResult, ...]) -> MeetingReviewContent:
        self.called_with = batches
        return self._delegate.merge(batches)


def _use_case(
    factory: _UnitOfWorkFactory,
    provider: _Provider,
    *,
    max_batch_characters: int = 100,
    merger: MeetingReviewMerger | None = None,
) -> GenerateMeetingReviewUseCase:
    return GenerateMeetingReviewUseCase(
        unit_of_work_factory=factory,
        meeting_review_generation_provider=provider,
        meeting_review_batcher=MeetingReviewBatcher(
            max_batch_characters=max_batch_characters
        ),
        meeting_review_merger=merger or MeetingReviewMerger(),
    )


def test_missing_meeting_raises_lookup_error_without_provider_or_commit() -> None:
    factory = _UnitOfWorkFactory(None)
    provider = _Provider()

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    assert provider.requests == []
    assert factory.created[0].commit_calls == 0
    assert factory.created[0].exited is True


@pytest.mark.parametrize("status", [MeetingStatus.DRAFT, MeetingStatus.ACTIVE])
def test_only_ended_meeting_can_generate_a_review(status: MeetingStatus) -> None:
    factory = _UnitOfWorkFactory(_detail(status=status))
    provider = _Provider()

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(_use_case(factory, provider).execute(_command()))

    assert provider.requests == []
    assert factory.created[0].commit_calls == 0


def test_zero_transcript_returns_deterministic_empty_content_without_provider() -> None:
    factory = _UnitOfWorkFactory(_detail())
    provider = _Provider()

    result = asyncio.run(_use_case(factory, provider).execute(_command()))

    assert result.content == MeetingReviewContent(
        summary="No transcript content was available for review.",
        key_decisions=(),
        action_items=(),
        open_questions=(),
        technical_questions=(),
        technical_terms=(),
        feedback=None,
    )
    assert result.reused_existing is False
    assert provider.requests == []
    assert factory.created[0].commit_calls == 0


def test_one_batch_calls_provider_then_final_reduce_with_merged_content() -> None:
    transcript = (_transcript_entry(0, "First."), _transcript_entry(1, "Second."))
    factory = _UnitOfWorkFactory(_detail(transcript=transcript))
    provider = _Provider()
    merger = _Merger()

    result = asyncio.run(
        _use_case(factory, provider, max_batch_characters=100, merger=merger).execute(
            _command()
        )
    )

    assert result.content.summary == "Final review."
    assert [request.stage for request in provider.requests] == [
        MeetingReviewGenerationStage.BATCH,
        MeetingReviewGenerationStage.REDUCE,
    ]
    assert provider.requests[0].batch is not None
    assert provider.requests[0].batch.texts == ("First.", "Second.")
    assert provider.requests[1].batch is None
    assert provider.requests[1].intermediate_content is not None
    assert provider.requests[1].intermediate_content.summary == "Batch 0."
    assert merger.called_with is not None
    assert factory.created[0].commit_calls == 0


def test_multiple_batches_are_generated_sequentially_from_batch_local_text() -> None:
    transcript = tuple(_transcript_entry(index, "12345") for index in range(4))
    factory = _UnitOfWorkFactory(_detail(transcript=transcript))
    provider = _Provider()

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
    ] == [
        0,
        1,
        2,
    ]
    assert [request.batch.texts for request in batch_requests if request.batch] == [
        ("12345", "12345"),
        ("12345", "12345"),
        ("12345", "12345"),
    ]
    assert provider.requests[-1].stage is MeetingReviewGenerationStage.REDUCE


def test_provider_errors_and_cancellation_propagate_without_partial_result() -> None:
    transcript = (_transcript_entry(0),)
    provider_error = ProviderUnavailableError("Provider unavailable.")

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(
            _use_case(
                _UnitOfWorkFactory(_detail(transcript=transcript)),
                _Provider(batch_error=provider_error),
            ).execute(_command())
        )
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(
            _use_case(
                _UnitOfWorkFactory(_detail(transcript=transcript)),
                _Provider(reduce_error=provider_error),
            ).execute(_command())
        )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            _use_case(
                _UnitOfWorkFactory(_detail(transcript=transcript)),
                _Provider(reduce_error=asyncio.CancelledError()),
            ).execute(_command())
        )


def test_long_transcript_is_not_truncated_or_resent_as_raw_reduce_input() -> None:
    transcript = tuple(_transcript_entry(index) for index in range(2_001))
    detail = _detail(transcript=transcript)
    factory = _UnitOfWorkFactory(detail)
    provider = _Provider()
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
        for transcript_id in request.batch.transcript_ids
        if request.batch is not None
    }
    reduce_request = provider.requests[-1]
    assert batch_ids == {entry.transcript_id for entry in transcript}
    assert len(provider.requests) == len(batch_requests) + 1
    assert reduce_request.stage is MeetingReviewGenerationStage.REDUCE
    assert reduce_request.batch is None
    assert reduce_request.intermediate_content is not None
    assert factory.created[0].commit_calls == 0
    assert detail.transcript == transcript


def test_command_requires_runtime_valid_meeting_id_and_boolean() -> None:
    with pytest.raises(ApplicationValidationError):
        GenerateMeetingReviewCommand(meeting_id="invalid")
    with pytest.raises(ApplicationValidationError):
        GenerateMeetingReviewCommand(meeting_id=_MEETING_ID, force_regenerate=1)


def _command() -> GenerateMeetingReviewCommand:
    return GenerateMeetingReviewCommand(meeting_id=_MEETING_ID)
