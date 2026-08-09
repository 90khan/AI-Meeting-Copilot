"""Tests for persisted Meeting translation artifact generation lifecycle."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.application.dto import AudioSource
from app.application.dto.ai import LanguageCode, TranslationResult
from app.application.dto.meeting_review import (
    GenerateMeetingTranslationCommand,
    MeetingDetail,
    MeetingTranslationArtifact,
    TranscriptReadItem,
    TranslationArtifactSegment,
    TranslationArtifactStatus,
)
from app.application.exceptions import ProviderError
from app.application.use_cases import GenerateMeetingTranslationUseCase
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId, MeetingStatus

_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000001"))
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_ARTIFACT_ID = UUID(int=99)


def _detail(
    *,
    status: MeetingStatus = MeetingStatus.ENDED,
    transcript_count: int = 2,
) -> MeetingDetail:
    transcript = tuple(
        TranscriptReadItem(
            transcript_id=UUID(int=index + 1),
            text=f" exact source {index} ",
            timestamp=_NOW + timedelta(seconds=index),
            speaker="Unknown",
            source=AudioSource.MIXED,
        )
        for index in range(transcript_count)
    )
    return MeetingDetail(
        meeting_id=_MEETING_ID,
        title="Translation test",
        status=status,
        created_at=_NOW,
        started_at=_NOW,
        ended_at=_NOW if status is MeetingStatus.ENDED else None,
        transcript=transcript,
        recording_available=False,
        recording_state=None,
        recording_duration_seconds=None,
        audio_expires_at=None,
        audio_protected=False,
        audio_has_gaps=False,
    )


def _completed_artifact(
    detail: MeetingDetail,
    *,
    artifact_id: UUID = _ARTIFACT_ID,
    version: int = 3,
) -> MeetingTranslationArtifact:
    return MeetingTranslationArtifact(
        artifact_id=artifact_id,
        meeting_id=detail.meeting_id,
        version=version,
        target_language="tr",
        status=TranslationArtifactStatus.COMPLETED,
        created_at=_NOW,
        completed_at=_NOW,
        source_transcript_count=len(detail.transcript),
        segments=tuple(
            TranslationArtifactSegment(
                transcript_id=item.transcript_id,
                source_text=item.text,
                translated_text=f"Turkish {index}",
            )
            for index, item in enumerate(detail.transcript)
        ),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_translation_v1",
        schema_version=1,
        failure_code=None,
    )


class _Harness:
    def __init__(self, detail: MeetingDetail | None) -> None:
        self.detail = detail
        self.artifacts: dict[UUID, MeetingTranslationArtifact] = {}
        self.units: list[_UnitOfWork] = []
        self.save_events: list[MeetingTranslationArtifact] = []
        self.fail_cancelled_save = False
        self.active_unit_of_work_count = 0

    def create_unit_of_work(self) -> "_UnitOfWork":
        unit_of_work = _UnitOfWork(self)
        self.units.append(unit_of_work)
        return unit_of_work


class _Reviews:
    def __init__(self, harness: _Harness) -> None:
        self._harness = harness

    async def get_meeting_detail(self, meeting_id: MeetingId) -> MeetingDetail | None:
        return self._harness.detail


class _Translations:
    def __init__(self, harness: _Harness) -> None:
        self._harness = harness

    async def get_latest_completed(
        self, meeting_id: MeetingId
    ) -> MeetingTranslationArtifact | None:
        completed = [
            artifact
            for artifact in self._harness.artifacts.values()
            if artifact.meeting_id == meeting_id
            and artifact.status is TranslationArtifactStatus.COMPLETED
        ]
        return max(completed, key=lambda artifact: artifact.version, default=None)

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
    ) -> int | None:
        versions = [
            artifact.version
            for artifact in self._harness.artifacts.values()
            if artifact.meeting_id == meeting_id
            and artifact.target_language == target_language
        ]
        return max(versions, default=None)

    async def get_by_id(self, artifact_id: UUID) -> MeetingTranslationArtifact | None:
        return self._harness.artifacts.get(artifact_id)

    async def save(self, artifact: MeetingTranslationArtifact) -> None:
        if (
            self._harness.fail_cancelled_save
            and artifact.status is TranslationArtifactStatus.CANCELLED
        ):
            raise RuntimeError("persistence failure")
        self._harness.artifacts[artifact.artifact_id] = artifact
        self._harness.save_events.append(artifact)


class _Provider:
    def __init__(
        self,
        harness: _Harness,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._harness = harness
        self._failure = failure
        self.requests: list[str] = []
        self.active_units_during_calls: list[int] = []

    async def translate(self, request: object) -> TranslationResult:
        self.active_units_during_calls.append(self._harness.active_unit_of_work_count)
        if self._failure is not None:
            raise self._failure
        text = request.text
        self.requests.append(text)
        return TranslationResult(
            translated_text=f"Turkish: {text}",
            source_language=LanguageCode(value="de"),
            target_language=LanguageCode(value="tr"),
        )


class _UnitOfWork:
    def __init__(self, harness: _Harness) -> None:
        self.meeting_reviews = _Reviews(harness)
        self.meeting_translations = _Translations(harness)
        self._harness = harness
        self.commit_calls = 0
        self.exited = False

    async def __aenter__(self) -> "_UnitOfWork":
        self._harness.active_unit_of_work_count += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        self._harness.active_unit_of_work_count -= 1
        self.exited = True

    async def commit(self) -> None:
        self.commit_calls += 1


def _use_case(
    detail: MeetingDetail | None,
    *,
    existing: MeetingTranslationArtifact | None = None,
    provider_failure: BaseException | None = None,
) -> tuple[GenerateMeetingTranslationUseCase, _Harness, _Provider]:
    harness = _Harness(detail)
    if existing is not None:
        harness.artifacts[existing.artifact_id] = existing
    provider = _Provider(harness, failure=provider_failure)
    use_case = GenerateMeetingTranslationUseCase(
        unit_of_work_factory=harness.create_unit_of_work,
        translation_provider=provider,
        utc_clock=lambda: _NOW,
        uuid_factory=lambda: UUID(int=1000),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_translation_v1",
        schema_version=1,
    )
    return use_case, harness, provider


def _execute(
    use_case: GenerateMeetingTranslationUseCase,
    *,
    force_regenerate: bool = False,
) -> object:
    return asyncio.run(
        use_case.execute(
            GenerateMeetingTranslationCommand(
                meeting_id=_MEETING_ID,
                force_regenerate=force_regenerate,
            )
        )
    )


def test_missing_meeting_does_not_write() -> None:
    use_case, harness, provider = _use_case(None)

    with pytest.raises(LookupError, match="Meeting not found"):
        _execute(use_case)

    assert harness.save_events == []
    assert provider.requests == []


@pytest.mark.parametrize("status", [MeetingStatus.DRAFT, MeetingStatus.ACTIVE])
def test_non_ended_meeting_does_not_write(status: MeetingStatus) -> None:
    use_case, harness, _ = _use_case(_detail(status=status))

    with pytest.raises(InvalidStateTransitionError, match="ended"):
        _execute(use_case)

    assert harness.save_events == []


def test_reuse_returns_unchanged_artifact_without_writes_or_provider_calls() -> None:
    detail = _detail()
    existing = _completed_artifact(detail)
    use_case, harness, provider = _use_case(detail, existing=existing)

    result = _execute(use_case)

    assert result.artifact is existing
    assert result.reused_existing is True
    assert harness.save_events == []
    assert provider.requests == []
    assert sum(unit.commit_calls for unit in harness.units) == 0


def test_processing_commits_before_provider_and_completion() -> None:
    detail = _detail()
    use_case, harness, provider = _use_case(detail)

    result = _execute(use_case)

    processing, completed = harness.save_events
    assert processing.status is TranslationArtifactStatus.PROCESSING
    assert completed.status is TranslationArtifactStatus.COMPLETED
    assert processing.artifact_id == completed.artifact_id
    assert completed.artifact_id == result.artifact.artifact_id
    assert processing.version == completed.version == result.artifact.version == 1
    assert processing.created_at == completed.created_at
    assert processing.provider_name == completed.provider_name
    assert processing.model_name == completed.model_name
    assert processing.prompt_version == completed.prompt_version
    assert processing.schema_version == completed.schema_version
    assert provider.active_units_during_calls == [0, 0]
    assert [unit.commit_calls for unit in harness.units] == [0, 1, 1]


def test_zero_transcript_runs_lifecycle_without_provider() -> None:
    use_case, harness, provider = _use_case(_detail(transcript_count=0))

    result = _execute(use_case)

    assert [artifact.status for artifact in harness.save_events] == [
        TranslationArtifactStatus.PROCESSING,
        TranslationArtifactStatus.COMPLETED,
    ]
    assert result.artifact.segments == ()
    assert provider.requests == []
    assert [unit.commit_calls for unit in harness.units] == [0, 1, 1]


def test_provider_failure_persists_failed_artifact_without_partial_segments() -> None:
    failure = ProviderError("provider unavailable")
    use_case, harness, _ = _use_case(_detail(), provider_failure=failure)

    with pytest.raises(ProviderError) as raised:
        _execute(use_case)

    processing, failed = harness.save_events
    assert raised.value is failure
    assert processing.artifact_id == failed.artifact_id
    assert processing.version == failed.version
    assert failed.status is TranslationArtifactStatus.FAILED
    assert failed.segments == ()
    assert failed.failure_code == "translation_provider_failed"
    assert [unit.commit_calls for unit in harness.units] == [0, 1, 1]


def test_cancellation_persists_cancelled_and_propagates() -> None:
    use_case, harness, _ = _use_case(
        _detail(),
        provider_failure=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        _execute(use_case)

    processing, cancelled = harness.save_events
    assert processing.artifact_id == cancelled.artifact_id
    assert cancelled.status is TranslationArtifactStatus.CANCELLED
    assert cancelled.segments == ()
    assert cancelled.failure_code == "translation_cancelled"


def test_cancellation_persistence_failure_still_propagates_cancellation() -> None:
    use_case, harness, _ = _use_case(
        _detail(),
        provider_failure=asyncio.CancelledError(),
    )
    harness.fail_cancelled_save = True

    with pytest.raises(asyncio.CancelledError):
        _execute(use_case)

    assert [artifact.status for artifact in harness.save_events] == [
        TranslationArtifactStatus.PROCESSING,
    ]


def test_force_regenerate_uses_next_version_and_keeps_previous_completed() -> None:
    detail = _detail()
    previous = _completed_artifact(detail)
    use_case, harness, _ = _use_case(detail, existing=previous)

    result = _execute(use_case, force_regenerate=True)

    assert result.artifact.version == previous.version + 1
    assert harness.artifacts[previous.artifact_id] is previous
    assert previous.status is TranslationArtifactStatus.COMPLETED
    assert [artifact.status for artifact in harness.save_events] == [
        TranslationArtifactStatus.PROCESSING,
        TranslationArtifactStatus.COMPLETED,
    ]


@pytest.mark.parametrize(
    "previous_status",
    [TranslationArtifactStatus.FAILED, TranslationArtifactStatus.CANCELLED],
)
def test_failed_and_cancelled_versions_count_toward_next_version(
    previous_status: TranslationArtifactStatus,
) -> None:
    detail = _detail()
    use_case, harness, _ = _use_case(detail)
    prior = MeetingTranslationArtifact(
        artifact_id=UUID(int=25),
        meeting_id=_MEETING_ID,
        version=7,
        target_language="tr",
        status=previous_status,
        created_at=_NOW,
        completed_at=None,
        source_transcript_count=2,
        segments=(),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_translation_v1",
        schema_version=1,
        failure_code=(
            "translation_provider_failed"
            if previous_status is TranslationArtifactStatus.FAILED
            else "translation_cancelled"
        ),
    )
    harness.artifacts[prior.artifact_id] = prior

    result = _execute(use_case)

    assert result.artifact.version == 8


def test_transcript_order_text_and_original_read_model_are_unchanged() -> None:
    detail = _detail(transcript_count=2_001)
    original_transcript = detail.transcript
    use_case, harness, provider = _use_case(detail)

    result = _execute(use_case)

    assert detail.transcript is original_transcript
    assert len(result.artifact.segments) == 2_001
    assert [segment.transcript_id for segment in result.artifact.segments] == [
        item.transcript_id for item in detail.transcript
    ]
    assert [segment.source_text for segment in result.artifact.segments] == [
        item.text for item in detail.transcript
    ]
    assert provider.requests == [item.text for item in detail.transcript]
    assert harness.save_events[-1].status is TranslationArtifactStatus.COMPLETED
