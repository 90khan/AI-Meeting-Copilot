"""Tests for immutable Meeting interview-review artifact DTOs."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
    ReviewActionItem,
    ReviewFeedback,
    ReviewInterviewQuestion,
    ReviewOpenQuestion,
    ReviewTechnicalTerm,
)
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MEETING_ID = MeetingId(UUID(int=1))
_ARTIFACT_ID = UUID(int=2)


def _content(**overrides: object) -> MeetingReviewContent:
    values: dict[str, object] = {
        "summary": "Concise review summary.",
        "key_decisions": ("Decision one.",),
        "action_items": (
            ReviewActionItem(text="Follow up.", owner=None, due_date=None),
        ),
        "open_questions": (ReviewOpenQuestion(question="What is next?"),),
        "technical_questions": (
            ReviewInterviewQuestion(
                question="How does it scale?",
                answer_summary=None,
                evaluation=None,
                improvement_suggestion=None,
            ),
        ),
        "technical_terms": (
            ReviewTechnicalTerm(term="Cache", explanation="Temporary storage."),
        ),
        "feedback": ReviewFeedback(
            strengths=("Clear explanation.",),
            improvement_areas=("Add examples.",),
            overall_feedback="Good overall performance.",
        ),
    }
    values.update(overrides)
    return MeetingReviewContent(**values)


def _artifact(
    status: MeetingReviewArtifactStatus = MeetingReviewArtifactStatus.COMPLETED,
    **overrides: object,
) -> MeetingReviewArtifact:
    completed = status is MeetingReviewArtifactStatus.COMPLETED
    values: dict[str, object] = {
        "artifact_id": _ARTIFACT_ID,
        "meeting_id": _MEETING_ID,
        "version": 1,
        "review_type": "interview_review",
        "status": status,
        "created_at": _NOW,
        "completed_at": _NOW if completed else None,
        "source_transcript_count": 3,
        "content": _content() if completed else None,
        "provider_name": "ollama",
        "model_name": "qwen2.5:3b",
        "prompt_version": "interview_review_v1",
        "schema_version": 1,
        "failure_code": (
            "review_provider_failed"
            if status is MeetingReviewArtifactStatus.FAILED
            else None
        ),
    }
    values.update(overrides)
    return MeetingReviewArtifact(**values)


def test_review_status_values_are_stable() -> None:
    assert [status.value for status in MeetingReviewArtifactStatus] == [
        "pending",
        "processing",
        "completed",
        "failed",
        "cancelled",
    ]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ReviewActionItem(text=" ", owner=None, due_date=None),
        lambda: ReviewActionItem(text="Action", owner=" ", due_date=None),
        lambda: ReviewActionItem(text="Action", owner=None, due_date=" "),
        lambda: ReviewOpenQuestion(question=" "),
        lambda: ReviewTechnicalTerm(term=" ", explanation="Explanation"),
        lambda: ReviewTechnicalTerm(term="Term", explanation=" "),
        lambda: ReviewInterviewQuestion(
            question=" ",
            answer_summary=None,
            evaluation=None,
            improvement_suggestion=None,
        ),
        lambda: ReviewFeedback(
            strengths=["Not immutable"],
            improvement_areas=(),
            overall_feedback="Feedback",
        ),
    ],
)
def test_nested_dtos_reject_blank_or_non_tuple_values(factory: object) -> None:
    with pytest.raises(ApplicationValidationError):
        factory()


def test_optional_nested_values_and_immutable_content_are_supported() -> None:
    action = ReviewActionItem(text=" Exact action ", owner=None, due_date=None)
    question = ReviewInterviewQuestion(
        question=" Exact question ",
        answer_summary=None,
        evaluation=None,
        improvement_suggestion=None,
    )
    content = _content(
        action_items=(action,),
        technical_questions=(question,),
        feedback=None,
    )

    assert action.text == " Exact action "
    assert question.question == " Exact question "
    assert content.feedback is None
    with pytest.raises(FrozenInstanceError):
        action.__setattr__("text", "Changed")
    with pytest.raises(FrozenInstanceError):
        content.__setattr__("summary", "Changed")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", " "),
        ("key_decisions", ["Not immutable"]),
        ("action_items", [ReviewActionItem(text="Action", owner=None, due_date=None)]),
        ("open_questions", [ReviewOpenQuestion(question="Question")]),
        ("technical_questions", []),
        ("technical_terms", []),
        ("feedback", "not feedback"),
    ],
)
def test_content_requires_valid_tuple_collections(field: str, value: object) -> None:
    with pytest.raises(ApplicationValidationError):
        _content(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("review_type", "meeting_review"),
        ("version", 0),
        ("schema_version", 0),
        ("source_transcript_count", -1),
        ("created_at", _NOW.replace(tzinfo=None)),
        ("completed_at", _NOW.replace(tzinfo=None)),
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
        (MeetingReviewArtifactStatus.COMPLETED, {"completed_at": None}),
        (MeetingReviewArtifactStatus.COMPLETED, {"content": None}),
        (MeetingReviewArtifactStatus.PENDING, {"content": _content()}),
        (
            MeetingReviewArtifactStatus.PROCESSING,
            {"failure_code": "review_provider_failed"},
        ),
        (MeetingReviewArtifactStatus.FAILED, {"failure_code": None}),
        (MeetingReviewArtifactStatus.FAILED, {"content": _content()}),
        (
            MeetingReviewArtifactStatus.CANCELLED,
            {"failure_code": "review_provider_failed"},
        ),
    ],
)
def test_artifact_enforces_lifecycle_invariants(
    status: MeetingReviewArtifactStatus,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ApplicationValidationError):
        _artifact(status, **overrides)


@pytest.mark.parametrize(
    "failure_code",
    ["review_provider_failed", "review_generation_failed", "review_cancelled"],
)
def test_failed_artifact_accepts_only_allowlisted_failure_codes(
    failure_code: str,
) -> None:
    assert _artifact(MeetingReviewArtifactStatus.FAILED, failure_code=failure_code)


def test_cancelled_artifact_may_include_only_cancellation_code() -> None:
    assert _artifact(MeetingReviewArtifactStatus.CANCELLED).failure_code is None
    assert (
        _artifact(
            MeetingReviewArtifactStatus.CANCELLED,
            failure_code="review_cancelled",
        ).failure_code
        == "review_cancelled"
    )


def test_artifact_is_immutable_and_excludes_forbidden_internal_fields() -> None:
    artifact = _artifact()

    with pytest.raises(FrozenInstanceError):
        artifact.__setattr__("version", 2)

    field_names = {field.name for field in fields(MeetingReviewArtifact)}
    assert {
        "prompt",
        "provider_response",
        "credentials",
        "storage_path",
        "key_reference",
        "session_id",
        "native_error",
    }.isdisjoint(field_names)
