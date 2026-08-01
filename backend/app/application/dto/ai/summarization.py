"""Meeting-summarization application data transfer objects."""

from dataclasses import dataclass

from app.application.dto.ai.language import LanguageCode
from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingSummaryRequest:
    """Input supplied to a meeting-summarization capability."""

    meeting_name: str
    transcript_text: str
    language: LanguageCode
    include_action_items: bool = True
    include_open_questions: bool = True

    def __post_init__(self) -> None:
        """Validate the meeting name and transcript text."""

        if not self.meeting_name.strip():
            raise ApplicationValidationError("Meeting name must not be blank.")
        if not self.transcript_text.strip():
            raise ApplicationValidationError("Transcript text must not be blank.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionItemDraft:
    """A proposed action item from a meeting summary."""

    text: str
    assignee: str | None = None

    def __post_init__(self) -> None:
        """Validate the action-item text and optional assignee."""

        if not self.text.strip():
            raise ApplicationValidationError("Action item text must not be blank.")
        if self.assignee is not None and not self.assignee.strip():
            raise ApplicationValidationError("Action item assignee must not be blank.")


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingSummaryResult:
    """A structured summary generated from a meeting transcript."""

    summary: str
    key_decisions: tuple[str, ...]
    action_items: tuple[ActionItemDraft, ...]
    open_questions: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate the summary and textual result collections."""

        if not self.summary.strip():
            raise ApplicationValidationError("Meeting summary must not be blank.")
        if any(not decision.strip() for decision in self.key_decisions):
            raise ApplicationValidationError("Key decision entries must not be blank.")
        if any(not question.strip() for question in self.open_questions):
            raise ApplicationValidationError("Open question entries must not be blank.")
