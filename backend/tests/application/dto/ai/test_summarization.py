"""Tests for meeting-summarization application DTOs."""

from dataclasses import FrozenInstanceError

import pytest
from app.application.dto.ai import (
    ActionItemDraft,
    LanguageCode,
    MeetingSummaryRequest,
    MeetingSummaryResult,
)
from app.application.exceptions import ApplicationValidationError


def test_meeting_summary_request_is_immutable() -> None:
    """Meeting-summary request fields cannot change after construction."""

    request = MeetingSummaryRequest(
        meeting_name="Planning",
        transcript_text="The team agreed on a plan.",
        language=LanguageCode(value="en"),
    )

    with pytest.raises(FrozenInstanceError):
        request.meeting_name = "Retrospective"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("meeting_name", "transcript_text"),
    [("", "A transcript"), ("Planning", "   ")],
)
def test_meeting_summary_request_rejects_blank_input(
    meeting_name: str, transcript_text: str
) -> None:
    """Meeting summaries require a name and transcript."""

    with pytest.raises(ApplicationValidationError):
        MeetingSummaryRequest(
            meeting_name=meeting_name,
            transcript_text=transcript_text,
            language=LanguageCode(value="en"),
        )


def test_action_item_draft_rejects_blank_text_or_assignee() -> None:
    """Action-item text and supplied assignees must not be blank."""

    with pytest.raises(ApplicationValidationError):
        ActionItemDraft(text="")
    with pytest.raises(ApplicationValidationError):
        ActionItemDraft(text="Send notes", assignee=" ")


def test_meeting_summary_result_is_immutable() -> None:
    """Meeting-summary result fields cannot change after construction."""

    result = MeetingSummaryResult(
        summary="The team agreed on a plan.",
        key_decisions=("Ship on Friday",),
        action_items=(ActionItemDraft(text="Send notes"),),
        open_questions=("Who owns deployment?",),
    )

    with pytest.raises(FrozenInstanceError):
        result.summary = "Updated"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("summary", "key_decisions", "open_questions"),
    [
        ("", (), ()),
        ("Summary", (" ",), ()),
        ("Summary", (), ("",)),
    ],
)
def test_meeting_summary_result_rejects_blank_content(
    summary: str, key_decisions: tuple[str, ...], open_questions: tuple[str, ...]
) -> None:
    """Summary text, decisions, and open questions must not be blank."""

    with pytest.raises(ApplicationValidationError):
        MeetingSummaryResult(
            summary=summary,
            key_decisions=key_decisions,
            action_items=(),
            open_questions=open_questions,
        )
