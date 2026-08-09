"""Tests for in-memory Meeting translation artifact generation."""

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


def _completed_artifact(detail: MeetingDetail) -> MeetingTranslationArtifact:
    return MeetingTranslationArtifact(
        artifact_id=UUID(int=99),
        meeting_id=detail.meeting_id,
        version=3,
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


class _Reviews:
    def __init__(self, detail: MeetingDetail | None) -> None:
        self.detail = detail
        self.calls = 0

    async def get_meeting_detail(self, meeting_id: MeetingId) -> MeetingDetail | None:
        self.calls += 1
        return self.detail


class _Translations:
    def __init__(self, existing: MeetingTranslationArtifact | None) -> None:
        self.existing = existing
        self.latest_version_calls = 0
        self.save_calls = 0

    async def get_latest_completed(
        self, meeting_id: MeetingId
    ) -> MeetingTranslationArtifact | None:
        return self.existing

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
    ) -> int | None:
        self.latest_version_calls += 1
        return None if self.existing is None else self.existing.version

    async def save(self, artifact: MeetingTranslationArtifact) -> None:
        self.save_calls += 1


class _Provider:
    def __init__(self, *, fail: ProviderError | None = None) -> None:
        self.requests: list[str] = []
        self._fail = fail

    async def translate(self, request: object) -> TranslationResult:
        if self._fail is not None:
            raise self._fail
        text = request.text
        self.requests.append(text)
        return TranslationResult(
            translated_text=f"Turkish: {text}",
            source_language=LanguageCode(value="de"),
            target_language=LanguageCode(value="tr"),
        )


class _UnitOfWork:
    def __init__(self, reviews: _Reviews, translations: _Translations) -> None:
        self.meeting_reviews = reviews
        self.meeting_translations = translations
        self.commit_calls = 0
        self.exited = False

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def commit(self) -> None:
        self.commit_calls += 1


def _use_case(
    detail: MeetingDetail | None,
    *,
    existing: MeetingTranslationArtifact | None = None,
    provider: _Provider | None = None,
) -> tuple[GenerateMeetingTranslationUseCase, _UnitOfWork, _Provider]:
    reviews = _Reviews(detail)
    translations = _Translations(existing)
    unit_of_work = _UnitOfWork(reviews, translations)
    resolved_provider = provider or _Provider()
    return (
        GenerateMeetingTranslationUseCase(
            unit_of_work_factory=lambda: unit_of_work,
            translation_provider=resolved_provider,
            utc_clock=lambda: _NOW,
            uuid_factory=lambda: UUID(int=1000),
            provider_name="ollama",
            model_name="qwen2.5:3b",
            prompt_version="meeting_translation_v1",
            schema_version=1,
        ),
        unit_of_work,
        resolved_provider,
    )


def test_missing_meeting_raises_lookup_error_without_provider_call() -> None:
    use_case, unit_of_work, provider = _use_case(None)

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(
            use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
        )

    assert provider.requests == []
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.exited is True


@pytest.mark.parametrize("status", [MeetingStatus.DRAFT, MeetingStatus.ACTIVE])
def test_only_ended_meetings_may_generate_translation(status: MeetingStatus) -> None:
    use_case, unit_of_work, provider = _use_case(_detail(status=status))

    with pytest.raises(InvalidStateTransitionError, match="ended"):
        asyncio.run(
            use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
        )

    assert provider.requests == []
    assert unit_of_work.commit_calls == 0


def test_reuse_skips_provider_and_persistence() -> None:
    detail = _detail()
    existing = _completed_artifact(detail)
    use_case, unit_of_work, provider = _use_case(detail, existing=existing)

    result = asyncio.run(
        use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
    )

    assert result.artifact is existing
    assert result.reused_existing is True
    assert provider.requests == []
    assert unit_of_work.meeting_translations.latest_version_calls == 0
    assert unit_of_work.meeting_translations.save_calls == 0
    assert unit_of_work.commit_calls == 0


def test_force_regenerate_translates_sequentially() -> None:
    detail = _detail()
    use_case, unit_of_work, provider = _use_case(
        detail,
        existing=_completed_artifact(detail),
    )

    result = asyncio.run(
        use_case.execute(
            GenerateMeetingTranslationCommand(
                meeting_id=_MEETING_ID,
                force_regenerate=True,
            )
        )
    )

    assert result.reused_existing is False
    assert result.artifact.version == 4
    assert provider.requests == [item.text for item in detail.transcript]
    assert unit_of_work.meeting_translations.save_calls == 0
    assert unit_of_work.commit_calls == 0


def test_zero_transcript_creates_completed_artifact_without_provider_call() -> None:
    use_case, unit_of_work, provider = _use_case(_detail(transcript_count=0))

    result = asyncio.run(
        use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
    )

    assert result.reused_existing is False
    assert result.artifact.status is TranslationArtifactStatus.COMPLETED
    assert result.artifact.segments == ()
    assert result.artifact.source_transcript_count == 0
    assert provider.requests == []
    assert unit_of_work.commit_calls == 0


def test_translation_preserves_persisted_order_ids_and_source_text() -> None:
    detail = _detail()
    use_case, _, provider = _use_case(detail)

    result = asyncio.run(
        use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
    )

    assert provider.requests == [item.text for item in detail.transcript]
    assert [segment.transcript_id for segment in result.artifact.segments] == [
        item.transcript_id for item in detail.transcript
    ]
    assert [segment.source_text for segment in result.artifact.segments] == [
        item.text for item in detail.transcript
    ]


def test_provider_error_propagates_without_creating_a_failed_artifact() -> None:
    failure = ProviderError("provider unavailable")
    use_case, unit_of_work, _ = _use_case(
        _detail(),
        provider=_Provider(fail=failure),
    )

    with pytest.raises(ProviderError) as raised:
        asyncio.run(
            use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
        )

    assert raised.value is failure
    assert unit_of_work.meeting_translations.save_calls == 0
    assert unit_of_work.commit_calls == 0


def test_long_transcript_is_fully_translated_without_batching_or_truncation() -> None:
    detail = _detail(transcript_count=2_001)
    use_case, unit_of_work, provider = _use_case(detail)

    result = asyncio.run(
        use_case.execute(GenerateMeetingTranslationCommand(meeting_id=_MEETING_ID))
    )

    assert len(provider.requests) == 2_001
    assert len(result.artifact.segments) == 2_001
    assert result.artifact.segments[0].source_text == " exact source 0 "
    assert result.artifact.segments[-1].source_text == " exact source 2000 "
    assert unit_of_work.meeting_translations.save_calls == 0
    assert unit_of_work.commit_calls == 0
