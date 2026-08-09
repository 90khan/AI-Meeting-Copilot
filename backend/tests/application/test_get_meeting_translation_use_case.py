"""Tests for completed Meeting translation artifact reads."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    GetMeetingTranslationQuery,
    MeetingTranslationArtifact,
    TranslationArtifactStatus,
)
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import GetMeetingTranslationUseCase
from app.domain.value_objects import MeetingId

_MEETING_ID = MeetingId(UUID(int=1))
_ARTIFACT_ID = UUID(int=2)
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _artifact(
    *,
    version: int = 1,
    status: TranslationArtifactStatus = TranslationArtifactStatus.COMPLETED,
) -> MeetingTranslationArtifact:
    return MeetingTranslationArtifact(
        artifact_id=_ARTIFACT_ID,
        meeting_id=_MEETING_ID,
        version=version,
        target_language="tr",
        status=status,
        created_at=_NOW,
        completed_at=_NOW if status is TranslationArtifactStatus.COMPLETED else None,
        source_transcript_count=0,
        segments=(),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_translation_v1",
        schema_version=1,
        failure_code=(
            "translation_provider_failed"
            if status is TranslationArtifactStatus.FAILED
            else None
        ),
    )


class _Translations:
    def __init__(self, artifact: MeetingTranslationArtifact | None) -> None:
        self.artifact = artifact
        self.latest_calls = 0
        self.exact_calls = 0
        self.save_calls = 0

    async def get_latest_completed(
        self, meeting_id: MeetingId
    ) -> MeetingTranslationArtifact | None:
        self.latest_calls += 1
        return self.artifact

    async def get_by_meeting_and_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
        version: int,
    ) -> MeetingTranslationArtifact | None:
        self.exact_calls += 1
        return self.artifact

    async def save(self, artifact: MeetingTranslationArtifact) -> None:
        self.save_calls += 1


class _UnitOfWork:
    def __init__(self, translations: _Translations) -> None:
        self.meeting_translations = translations
        self.commit_calls = 0
        self.exited = False

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def commit(self) -> None:
        self.commit_calls += 1


def test_latest_completed_artifact_is_returned_without_commit() -> None:
    artifact = _artifact()
    translations = _Translations(artifact)
    unit_of_work = _UnitOfWork(translations)

    result = asyncio.run(
        GetMeetingTranslationUseCase(lambda: unit_of_work).execute(
            GetMeetingTranslationQuery(meeting_id=_MEETING_ID)
        )
    )

    assert result.artifact is artifact
    assert translations.latest_calls == 1
    assert translations.exact_calls == 0
    assert translations.save_calls == 0
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.exited is True


def test_explicit_completed_version_returns_exact_artifact() -> None:
    artifact = _artifact(version=4)
    translations = _Translations(artifact)

    result = asyncio.run(
        GetMeetingTranslationUseCase(lambda: _UnitOfWork(translations)).execute(
            GetMeetingTranslationQuery(meeting_id=_MEETING_ID, version=4)
        )
    )

    assert result.artifact == artifact
    assert translations.latest_calls == 0
    assert translations.exact_calls == 1


@pytest.mark.parametrize(
    "artifact",
    [
        None,
        _artifact(status=TranslationArtifactStatus.FAILED),
        _artifact(status=TranslationArtifactStatus.CANCELLED),
    ],
)
def test_missing_or_non_completed_artifact_is_not_publicly_readable(
    artifact: MeetingTranslationArtifact | None,
) -> None:
    use_case = GetMeetingTranslationUseCase(
        lambda: _UnitOfWork(_Translations(artifact))
    )
    with pytest.raises(LookupError, match="Meeting translation not found"):
        asyncio.run(
            use_case.execute(
                GetMeetingTranslationQuery(meeting_id=_MEETING_ID, version=1)
            )
        )


@pytest.mark.parametrize("version", [0, -1, True])
def test_invalid_version_is_rejected(version: int) -> None:
    with pytest.raises(ApplicationValidationError):
        GetMeetingTranslationQuery(meeting_id=_MEETING_ID, version=version)
