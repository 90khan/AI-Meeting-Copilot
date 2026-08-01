"""Ollama adapter for the meeting-summarization application capability."""

from app.application.dto.ai import (
    ActionItemDraft,
    MeetingSummaryRequest,
    MeetingSummaryResult,
)
from app.application.exceptions import InvalidProviderResponseError
from app.infrastructure.providers.ollama.client import OllamaClient

_MEETING_SUMMARY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "assignee": {"type": ["string", "null"]},
                },
                "required": ["text", "assignee"],
                "additionalProperties": False,
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_decisions", "action_items", "open_questions"],
    "additionalProperties": False,
}


class OllamaMeetingSummarizationProvider:
    """Summarize meeting transcripts through a local Ollama model."""

    def __init__(self, *, client: OllamaClient, model: str) -> None:
        """Initialize the adapter with its shared client and configured model."""

        self._client = client
        self._model = model

    async def summarize(
        self,
        request: MeetingSummaryRequest,
    ) -> MeetingSummaryResult:
        """Summarize a transcript and validate its capability-specific response."""

        system_prompt, user_prompt = _build_meeting_summary_prompts(request)
        payload = await self._client.generate_structured(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=_MEETING_SUMMARY_SCHEMA,
        )

        try:
            summary = payload["summary"]
            decisions_payload = payload["key_decisions"]
            action_items_payload = payload["action_items"]
            questions_payload = payload["open_questions"]
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("Summary must be non-blank text.")
            if not isinstance(decisions_payload, list):
                raise TypeError("Key decisions must be a list.")
            if not isinstance(action_items_payload, list):
                raise TypeError("Action items must be a list.")
            if not isinstance(questions_payload, list):
                raise TypeError("Open questions must be a list.")

            key_decisions: list[str] = []
            for decision in decisions_payload:
                if not isinstance(decision, str) or not decision.strip():
                    raise ValueError("Key decisions must be non-blank strings.")
                key_decisions.append(decision)

            action_items: list[ActionItemDraft] = []
            for item in action_items_payload:
                if not isinstance(item, dict):
                    raise TypeError("Each action item must be an object.")
                text = item["text"]
                assignee = item["assignee"]
                if not isinstance(text, str):
                    raise TypeError("Action-item text must be a string.")
                if assignee is not None and not isinstance(assignee, str):
                    raise TypeError("Action-item assignee must be a string or null.")
                action_items.append(ActionItemDraft(text=text, assignee=assignee))

            open_questions: list[str] = []
            for question in questions_payload:
                if not isinstance(question, str) or not question.strip():
                    raise ValueError("Open questions must be non-blank strings.")
                open_questions.append(question)

            result_action_items = (
                tuple(action_items) if request.include_action_items else ()
            )
            result_open_questions = (
                tuple(open_questions) if request.include_open_questions else ()
            )
            return MeetingSummaryResult(
                summary=summary,
                key_decisions=tuple(key_decisions),
                action_items=result_action_items,
                open_questions=result_open_questions,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidProviderResponseError(
                "Ollama returned an invalid meeting summary payload."
            ) from error


def _build_meeting_summary_prompts(
    request: MeetingSummaryRequest,
) -> tuple[str, str]:
    """Build deterministic prompts for one meeting-summary request."""

    action_items_instruction = (
        "Include action items."
        if request.include_action_items
        else "Use no action items."
    )
    open_questions_instruction = (
        "Include open questions."
        if request.include_open_questions
        else "Use no open questions."
    )
    system_prompt = (
        "Summarize only the supplied transcript. Produce a concise, factual summary "
        f"in {request.language}. Extract key decisions. {action_items_instruction} "
        f"{open_questions_instruction} Never invent decisions, assignees, "
        "action items, "
        "or open questions. Use null for unknown action-item assignees. Do not add "
        "Markdown, headings, commentary, or explanations outside JSON. "
        "Output JSON only."
    )
    user_prompt = (
        f"Meeting name: {request.meeting_name}\n"
        "Transcript:\n"
        f"{request.transcript_text}"
    )
    return system_prompt, user_prompt
