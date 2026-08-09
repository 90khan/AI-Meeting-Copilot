"""Tests for safe Meeting translation HTTP routes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.api.routes import meeting_translation
from app.application.dto.meeting_review import (
    GenerateMeetingTranslationResult,
    MeetingTranslationArtifact,
    TranslationArtifactSegment,
    TranslationArtifactStatus,
)
from app.application.exceptions import ProviderError
from app.application.use_cases import GetMeetingTranslationUseCase
from app.domain.value_objects import MeetingId
from fastapi import FastAPI
from fastapi.testclient import TestClient

_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000001"))
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000002")
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _artifact(
    *,
    version: int = 1,
    status: TranslationArtifactStatus = TranslationArtifactStatus.COMPLETED,
    empty: bool = False,
) -> MeetingTranslationArtifact:
    segments = (
        ()
        if empty
        else (
            TranslationArtifactSegment(
                transcript_id=UUID(int=10),
                source_text=" exact first source ",
                translated_text=" exact first translation ",
            ),
            TranslationArtifactSegment(
                transcript_id=UUID(int=11),
                source_text=" exact second source ",
                translated_text=" exact second translation ",
            ),
        )
    )
    return MeetingTranslationArtifact(
        artifact_id=_ARTIFACT_ID,
        meeting_id=_MEETING_ID,
        version=version,
        target_language="tr",
        status=status,
        created_at=_NOW,
        completed_at=_NOW if status is TranslationArtifactStatus.COMPLETED else None,
        source_transcript_count=len(segments),
        segments=segments,
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

    async def get_latest_completed(
        self, meeting_id: MeetingId
    ) -> MeetingTranslationArtifact | None:
        return self.artifact

    async def get_by_meeting_and_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
        version: int,
    ) -> MeetingTranslationArtifact | None:
        return self.artifact


class _UnitOfWork:
    def __init__(self, artifact: MeetingTranslationArtifact | None) -> None:
        self.meeting_translations = _Translations(artifact)

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Generate:
    def __init__(
        self,
        result: GenerateMeetingTranslationResult | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.commands: list[object] = []

    async def execute(self, command: object) -> GenerateMeetingTranslationResult:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _client(
    artifact: MeetingTranslationArtifact | None,
    generator: _Generate | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(meeting_translation.router)
    app.state.container = SimpleNamespace(
        get_generate_meeting_translation_use_case=lambda: generator,
        get_get_meeting_translation_use_case=lambda: GetMeetingTranslationUseCase(
            lambda: _UnitOfWork(artifact)
        ),
    )
    return TestClient(app)


def test_post_returns_safe_generated_artifact_and_preserves_segment_order() -> None:
    artifact = _artifact()
    generator = _Generate(
        GenerateMeetingTranslationResult(artifact=artifact, reused_existing=False)
    )
    response = _client(artifact, generator).post(
        f"/api/v1/meetings/{_MEETING_ID}/translation",
        json={"force_regenerate": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_id"] == str(_ARTIFACT_ID)
    assert body["meeting_id"] == str(_MEETING_ID)
    assert body["created_at"].endswith("+00:00")
    assert [item["transcript_id"] for item in body["segments"]] == [
        str(segment.transcript_id) for segment in artifact.segments
    ]
    assert [item["source_text"] for item in body["segments"]] == [
        segment.source_text for segment in artifact.segments
    ]
    assert body["reused_existing"] is False
    _assert_no_internal_fields(body)


def test_post_supports_reuse_force_and_zero_transcript_artifacts() -> None:
    artifact = _artifact(version=4, empty=True)
    generator = _Generate(
        GenerateMeetingTranslationResult(artifact=artifact, reused_existing=True)
    )
    response = _client(artifact, generator).post(
        f"/api/v1/meetings/{_MEETING_ID}/translation",
        json={"force_regenerate": True},
    )

    assert response.status_code == 200
    assert response.json()["reused_existing"] is True
    assert response.json()["segments"] == []
    assert generator.commands[0].force_regenerate is True


def test_post_maps_provider_failure_to_generic_response() -> None:
    generator = _Generate(error=ProviderError("private provider response"))

    response = _client(None, generator).post(
        f"/api/v1/meetings/{_MEETING_ID}/translation",
        json={},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Meeting translation is unavailable."
    assert "private" not in response.text


def test_get_returns_latest_and_exact_completed_artifact() -> None:
    artifact = _artifact(version=3)
    client = _client(artifact)

    latest = client.get(f"/api/v1/meetings/{_MEETING_ID}/translation")
    exact = client.get(f"/api/v1/meetings/{_MEETING_ID}/translation?version=3")

    assert latest.status_code == 200
    assert exact.status_code == 200
    assert latest.json()["version"] == 3
    assert "reused_existing" not in latest.json()
    _assert_no_internal_fields(latest.json())


def test_get_maps_missing_and_invalid_parameters_safely() -> None:
    client = _client(None)

    missing = client.get(f"/api/v1/meetings/{_MEETING_ID}/translation")
    malformed = client.get("/api/v1/meetings/not-a-uuid/translation")
    invalid_version = client.get(
        f"/api/v1/meetings/{_MEETING_ID}/translation?version=0"
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Meeting translation not found"
    assert malformed.status_code == 422
    assert invalid_version.status_code == 422


def test_get_hides_non_completed_artifacts() -> None:
    artifact = _artifact(status=TranslationArtifactStatus.FAILED, empty=True)

    response = _client(artifact).get(f"/api/v1/meetings/{_MEETING_ID}/translation")

    assert response.status_code == 404


def _assert_no_internal_fields(body: dict[str, object]) -> None:
    assert {
        "provider_name",
        "model_name",
        "prompt_version",
        "schema_version",
        "failure_code",
        "storage_directory_token",
        "key_reference",
        "path",
    }.isdisjoint(body)
