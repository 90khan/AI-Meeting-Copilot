"""Ollama adapter for structured hierarchical Meeting review generation."""

import json

from app.application.dto.meeting_review import (
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
    MeetingReviewGenerationStage,
    ReviewActionItem,
    ReviewFeedback,
    ReviewInterviewQuestion,
    ReviewOpenQuestion,
    ReviewTechnicalTerm,
)
from app.application.exceptions import InvalidProviderResponseError
from app.infrastructure.providers.ollama.client import OllamaClient

MEETING_REVIEW_BATCH_PROMPT_VERSION = "meeting_review_batch_v1"
MEETING_REVIEW_REDUCE_PROMPT_VERSION = "meeting_review_reduce_v1"

_MEETING_REVIEW_SCHEMA: dict[str, object] = {
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
                    "owner": {"type": ["string", "null"]},
                    "due_date": {"type": ["string", "null"]},
                },
                "required": ["text", "owner", "due_date"],
                "additionalProperties": False,
            },
        },
        "open_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
        },
        "technical_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer_summary": {"type": ["string", "null"]},
                    "evaluation": {"type": ["string", "null"]},
                    "improvement_suggestion": {"type": ["string", "null"]},
                },
                "required": [
                    "question",
                    "answer_summary",
                    "evaluation",
                    "improvement_suggestion",
                ],
                "additionalProperties": False,
            },
        },
        "technical_terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["term", "explanation"],
                "additionalProperties": False,
            },
        },
        "feedback": {
            "type": ["object", "null"],
            "properties": {
                "strengths": {"type": "array", "items": {"type": "string"}},
                "improvement_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "overall_feedback": {"type": "string"},
            },
            "required": ["strengths", "improvement_areas", "overall_feedback"],
            "additionalProperties": False,
        },
    },
    "required": [
        "summary",
        "key_decisions",
        "action_items",
        "open_questions",
        "technical_questions",
        "technical_terms",
        "feedback",
    ],
    "additionalProperties": False,
}


class OllamaMeetingReviewGenerationProvider:
    """Generate typed batch and final-review content through a local Ollama model."""

    def __init__(self, *, client: OllamaClient, model: str) -> None:
        self._client = client
        self._model = model

    async def generate_review(
        self,
        request: MeetingReviewGenerationRequest,
    ) -> MeetingReviewContent:
        """Generate and strictly validate one batch or final reduce review response."""

        system_prompt, user_prompt = _build_review_prompts(request)
        payload = await self._client.generate_structured(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=_MEETING_REVIEW_SCHEMA,
        )
        return _content_from_payload(payload)


def _build_review_prompts(
    request: MeetingReviewGenerationRequest,
) -> tuple[str, str]:
    """Build one deterministic privacy-preserving prompt pair for the request stage."""

    if request.stage is MeetingReviewGenerationStage.BATCH:
        assert request.batch is not None
        system_prompt = (
            f"Prompt version: {MEETING_REVIEW_BATCH_PROMPT_VERSION}. "
            "Review only the supplied transcript batch. Return JSON only, with no "
            "Markdown or commentary. Do not invent facts. Clearly mark unanswered or "
            "uncertain interview questions. Keep technical evaluations evidence-based."
        )
        user_prompt = json.dumps(
            {
                "batch_index": request.batch.batch_index,
                "transcript": list(request.batch.texts),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return system_prompt, user_prompt

    assert request.intermediate_content is not None
    system_prompt = (
        f"Prompt version: {MEETING_REVIEW_REDUCE_PROMPT_VERSION}. "
        "Polish only the supplied intermediate review. Return JSON only, with no "
        "Markdown or commentary. Create a coherent final summary, remove exact "
        "overlap repetition, preserve meaningful decisions, actions, questions, "
        "technical feedback, and uncertainty."
    )
    return system_prompt, json.dumps(
        _content_to_payload(request.intermediate_content),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _content_from_payload(payload: dict[str, object]) -> MeetingReviewContent:
    """Map an exact review JSON object into immutable application DTOs."""

    if set(payload) != {
        "summary",
        "key_decisions",
        "action_items",
        "open_questions",
        "technical_questions",
        "technical_terms",
        "feedback",
    }:
        raise InvalidProviderResponseError(
            "Ollama returned an invalid meeting review payload."
        )
    try:
        return MeetingReviewContent(
            summary=_string(payload["summary"]),
            key_decisions=_strings(payload["key_decisions"]),
            action_items=_action_items(payload["action_items"]),
            open_questions=_open_questions(payload["open_questions"]),
            technical_questions=_technical_questions(payload["technical_questions"]),
            technical_terms=_technical_terms(payload["technical_terms"]),
            feedback=_feedback(payload["feedback"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidProviderResponseError(
            "Ollama returned an invalid meeting review payload."
        ) from error


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError
    return tuple(_string(item) for item in value)


def _action_items(value: object) -> tuple[ReviewActionItem, ...]:
    if not isinstance(value, list):
        raise TypeError
    items: list[ReviewActionItem] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"text", "owner", "due_date"}:
            raise TypeError
        items.append(
            ReviewActionItem(
                text=_string(item["text"]),
                owner=_optional_string(item["owner"]),
                due_date=_optional_string(item["due_date"]),
            )
        )
    return tuple(items)


def _open_questions(value: object) -> tuple[ReviewOpenQuestion, ...]:
    if not isinstance(value, list):
        raise TypeError
    questions: list[ReviewOpenQuestion] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"question"}:
            raise TypeError
        questions.append(ReviewOpenQuestion(question=_string(item["question"])))
    return tuple(questions)


def _technical_questions(value: object) -> tuple[ReviewInterviewQuestion, ...]:
    if not isinstance(value, list):
        raise TypeError
    questions: list[ReviewInterviewQuestion] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "question",
            "answer_summary",
            "evaluation",
            "improvement_suggestion",
        }:
            raise TypeError
        questions.append(
            ReviewInterviewQuestion(
                question=_string(item["question"]),
                answer_summary=_optional_string(item["answer_summary"]),
                evaluation=_optional_string(item["evaluation"]),
                improvement_suggestion=_optional_string(item["improvement_suggestion"]),
            )
        )
    return tuple(questions)


def _technical_terms(value: object) -> tuple[ReviewTechnicalTerm, ...]:
    if not isinstance(value, list):
        raise TypeError
    terms: list[ReviewTechnicalTerm] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"term", "explanation"}:
            raise TypeError
        terms.append(
            ReviewTechnicalTerm(
                term=_string(item["term"]),
                explanation=_string(item["explanation"]),
            )
        )
    return tuple(terms)


def _feedback(value: object) -> ReviewFeedback | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "strengths",
        "improvement_areas",
        "overall_feedback",
    }:
        raise TypeError
    return ReviewFeedback(
        strengths=_strings(value["strengths"]),
        improvement_areas=_strings(value["improvement_areas"]),
        overall_feedback=_string(value["overall_feedback"]),
    )


def _content_to_payload(content: MeetingReviewContent) -> dict[str, object]:
    """Serialize only structured intermediate review content for final reduction."""

    return {
        "summary": content.summary,
        "key_decisions": list(content.key_decisions),
        "action_items": [
            {"text": item.text, "owner": item.owner, "due_date": item.due_date}
            for item in content.action_items
        ],
        "open_questions": [
            {"question": item.question} for item in content.open_questions
        ],
        "technical_questions": [
            {
                "question": item.question,
                "answer_summary": item.answer_summary,
                "evaluation": item.evaluation,
                "improvement_suggestion": item.improvement_suggestion,
            }
            for item in content.technical_questions
        ],
        "technical_terms": [
            {"term": item.term, "explanation": item.explanation}
            for item in content.technical_terms
        ],
        "feedback": (
            None
            if content.feedback is None
            else {
                "strengths": list(content.feedback.strengths),
                "improvement_areas": list(content.feedback.improvement_areas),
                "overall_feedback": content.feedback.overall_feedback,
            }
        ),
    }
