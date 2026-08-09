"""Tests for the Ollama structured Meeting review generation adapter."""

import asyncio
import json
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
    MeetingReviewGenerationStage,
    TranscriptReviewBatch,
)
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.domain.value_objects import MeetingId
from app.infrastructure.providers.ollama.meeting_review_generation import (
    MEETING_REVIEW_BATCH_PROMPT_VERSION,
    MEETING_REVIEW_REDUCE_PROMPT_VERSION,
    OllamaMeetingReviewGenerationProvider,
)

_MEETING_ID = MeetingId(UUID(int=1))


def _payload() -> dict[str, object]:
    return {
        "summary": "Review summary.",
        "key_decisions": ["Decision one.", "Decision two."],
        "action_items": [
            {"text": "Send notes.", "owner": "Owner", "due_date": None},
            {"text": "Review plan.", "owner": None, "due_date": "Tomorrow"},
        ],
        "open_questions": [{"question": "Who approves?"}],
        "technical_questions": [
            {
                "question": "How does caching work?",
                "answer_summary": "It avoids repeated reads.",
                "evaluation": "Evidence based.",
                "improvement_suggestion": None,
            },
        ],
        "technical_terms": [
            {"term": "Cache", "explanation": "A fast data store."},
        ],
        "feedback": {
            "strengths": ["Clear."],
            "improvement_areas": ["More examples."],
            "overall_feedback": "Strong review.",
        },
    }


def _content() -> MeetingReviewContent:
    return MeetingReviewContent(
        summary="Merged intermediate review.",
        key_decisions=("Decision one.",),
        action_items=(),
        open_questions=(),
        technical_questions=(),
        technical_terms=(),
        feedback=None,
    )


def _batch_request() -> MeetingReviewGenerationRequest:
    return MeetingReviewGenerationRequest(
        meeting_id=_MEETING_ID,
        stage=MeetingReviewGenerationStage.BATCH,
        batch=TranscriptReviewBatch(
            batch_index=3,
            start_transcript_index=10,
            end_transcript_index=11,
            transcript_ids=(UUID(int=10), UUID(int=11)),
            texts=("Batch-only text one.", "Batch-only text two."),
            total_characters=len("Batch-only text one.") + len("Batch-only text two."),
        ),
        intermediate_content=None,
    )


def _reduce_request() -> MeetingReviewGenerationRequest:
    return MeetingReviewGenerationRequest(
        meeting_id=_MEETING_ID,
        stage=MeetingReviewGenerationStage.REDUCE,
        batch=None,
        intermediate_content=_content(),
    )


class _Client:
    def __init__(self, response: dict[str, object] | BaseException) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def generate_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": schema,
            }
        )
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


def test_valid_batch_response_maps_exactly_and_uses_only_batch_text() -> None:
    client = _Client(_payload())
    provider = OllamaMeetingReviewGenerationProvider(client=client, model="review")

    result = asyncio.run(provider.generate_review(_batch_request()))

    assert result.summary == "Review summary."
    assert result.key_decisions == ("Decision one.", "Decision two.")
    assert [item.text for item in result.action_items] == [
        "Send notes.",
        "Review plan.",
    ]
    assert [item.question for item in result.open_questions] == ["Who approves?"]
    assert result.feedback is not None
    assert result.feedback.strengths == ("Clear.",)
    assert client.calls[0]["model"] == "review"
    assert MEETING_REVIEW_BATCH_PROMPT_VERSION in client.calls[0]["system_prompt"]
    assert json.loads(client.calls[0]["user_prompt"]) == {
        "batch_index": 3,
        "transcript": ["Batch-only text one.", "Batch-only text two."],
    }


def test_valid_reduce_response_uses_only_merged_intermediate_content() -> None:
    client = _Client(_payload())
    provider = OllamaMeetingReviewGenerationProvider(client=client, model="review")

    result = asyncio.run(provider.generate_review(_reduce_request()))

    assert result.summary == "Review summary."
    assert MEETING_REVIEW_REDUCE_PROMPT_VERSION in client.calls[0]["system_prompt"]
    reduce_payload = json.loads(client.calls[0]["user_prompt"])
    assert reduce_payload["summary"] == "Merged intermediate review."
    assert "Batch-only text one." not in client.calls[0]["user_prompt"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**_payload(), "unexpected": "value"},
        {key: value for key, value in _payload().items() if key != "feedback"},
        {**_payload(), "action_items": "not-a-list"},
        {
            **_payload(),
            "action_items": [{"text": "Missing fields."}],
        },
        {
            **_payload(),
            "technical_questions": [
                {
                    "question": "Question?",
                    "answer_summary": 1,
                    "evaluation": None,
                    "improvement_suggestion": None,
                },
            ],
        },
        {
            **_payload(),
            "feedback": {"strengths": [], "improvement_areas": []},
        },
    ],
)
def test_invalid_structured_payloads_become_privacy_safe_provider_errors(
    payload: dict[str, object],
) -> None:
    provider = OllamaMeetingReviewGenerationProvider(
        client=_Client(payload),
        model="review",
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        asyncio.run(provider.generate_review(_batch_request()))

    assert "Batch-only text" not in str(captured.value)
    assert "unexpected" not in str(captured.value)


@pytest.mark.parametrize(
    "error",
    [
        InvalidProviderResponseError("Ollama returned invalid JSON."),
        ProviderUnavailableError(),
        ProviderTimeoutError(),
    ],
)
def test_shared_client_provider_errors_propagate_without_raw_content(
    error: BaseException,
) -> None:
    provider = OllamaMeetingReviewGenerationProvider(
        client=_Client(error),
        model="review",
    )

    with pytest.raises(type(error)) as captured:
        asyncio.run(provider.generate_review(_batch_request()))

    assert "Batch-only text" not in str(captured.value)


def test_cancellation_propagates_unchanged() -> None:
    provider = OllamaMeetingReviewGenerationProvider(
        client=_Client(asyncio.CancelledError()),
        model="review",
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.generate_review(_batch_request()))
