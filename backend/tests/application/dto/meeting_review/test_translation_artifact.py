"""Tests for versioned Meeting translation artifact DTOs."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    MeetingTranslationArtifact,
    TranslationArtifactSegment,
    TranslationArtifactStatus,
)
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_CREATED_AT = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000001"))
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000002")
_TRANSCRIPT_ID = UUID("00000000-0000-0000-0000-000000000003")


def _segment(**overrides: object) -> TranslationArtifactSegment:
    values: dict[str, object] = {
        "transcript_id": _TRANSCRIPT_ID,
        "source_text": "  Guten Morgen.  ",
        "translated_text": "  Ceviri.  ",
    }
    values.update(overrides)
    return TranslationArtifactSegment(**values)


def _artifact(
    status: TranslationArtifactStatus = TranslationArtifactStatus.COMPLETED,
    **overrides: object,
) -> MeetingTranslationArtifact:
    is_completed = status is TranslationArtifactStatus.COMPLETED
    values: dict[str, object] = {
        "artifact_id": _ARTIFACT_ID,
        "meeting_id": _MEETING_ID,
        "version": 1,
        "target_language": "tr",
        "status": status,
        "created_at": _CREATED_AT,
        "completed_at": _CREATED_AT if is_completed else None,
        "source_transcript_count": 1 if is_completed else 0,
        "segments": (_segment(),) if is_completed else (),
        "provider_name": "ollama",
        "model_name": "qwen2.5:3b",
        "prompt_version": "meeting_translation_v1",
        "schema_version": 1,
        "failure_code": (
            "translation_provider_failed"
            if status is TranslationArtifactStatus.FAILED
            else None
        ),
    }
    values.update(overrides)
    return MeetingTranslationArtifact(**values)


def test_translation_artifact_status_values_are_stable() -> None:
    assert [status.value for status in TranslationArtifactStatus] == [
        "pending",
        "processing",
        "completed",
        "failed",
        "cancelled",
    ]


def test_segment_preserves_text_exactly_and_is_immutable() -> None:
    segment = _segment()

    assert segment.source_text == "  Guten Morgen.  "
    assert segment.translated_text == "  Ceviri.  "
    with pytest.raises(FrozenInstanceError):
        segment.__setattr__("source_text", "Changed")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transcript_id", str(_TRANSCRIPT_ID)),
        ("source_text", "   "),
        ("translated_text", "   "),
    ],
)
def test_segment_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ApplicationValidationError):
        _segment(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_language", "de"),
        ("version", 0),
        ("schema_version", 0),
        ("source_transcript_count", -1),
        ("created_at", _CREATED_AT.replace(tzinfo=None)),
        ("completed_at", _CREATED_AT.replace(tzinfo=None)),
        ("segments", [_segment()]),
        ("provider_name", " "),
        ("model_name", " "),
        ("prompt_version", " "),
    ],
)
def test_artifact_rejects_invalid_general_values(field: str, value: object) -> None:
    with pytest.raises(ApplicationValidationError):
        _artifact(**{field: value})


@pytest.mark.parametrize(
    ("status", "overrides"),
    [
        (TranslationArtifactStatus.COMPLETED, {"completed_at": None}),
        (
            TranslationArtifactStatus.COMPLETED,
            {"failure_code": "translation_provider_failed"},
        ),
        (TranslationArtifactStatus.PENDING, {"segments": (_segment(),)}),
        (
            TranslationArtifactStatus.PROCESSING,
            {"failure_code": "translation_provider_failed"},
        ),
        (TranslationArtifactStatus.FAILED, {"failure_code": None}),
        (TranslationArtifactStatus.FAILED, {"segments": (_segment(),)}),
        (
            TranslationArtifactStatus.CANCELLED,
            {"failure_code": "translation_provider_failed"},
        ),
    ],
)
def test_artifact_enforces_status_invariants(
    status: TranslationArtifactStatus,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ApplicationValidationError):
        _artifact(status, **overrides)


@pytest.mark.parametrize(
    "failure_code",
    ["translation_provider_failed", "translation_cancelled"],
)
def test_failed_artifact_accepts_only_allowlisted_failure_codes(
    failure_code: str,
) -> None:
    artifact = _artifact(TranslationArtifactStatus.FAILED, failure_code=failure_code)

    assert artifact.failure_code == failure_code


def test_cancelled_artifact_may_include_only_cancellation_code() -> None:
    assert _artifact(TranslationArtifactStatus.CANCELLED).failure_code is None
    assert (
        _artifact(
            TranslationArtifactStatus.CANCELLED,
            failure_code="translation_cancelled",
        ).failure_code
        == "translation_cancelled"
    )


def test_unknown_failure_code_is_rejected() -> None:
    with pytest.raises(ApplicationValidationError):
        _artifact(
            TranslationArtifactStatus.FAILED,
            failure_code="provider returned traceback and secret",
        )


def test_non_string_failure_code_is_rejected_as_malformed_input() -> None:
    with pytest.raises(ApplicationValidationError):
        _artifact(TranslationArtifactStatus.FAILED, failure_code=[])


def test_completed_artifact_is_immutable_and_has_only_safe_public_fields() -> None:
    artifact = _artifact()

    with pytest.raises(FrozenInstanceError):
        artifact.__setattr__("version", 2)

    field_names = {field.name for field in fields(MeetingTranslationArtifact)}
    assert {
        "credentials",
        "prompt_text",
        "storage_path",
        "key_reference",
        "provider_response",
    }.isdisjoint(field_names)
