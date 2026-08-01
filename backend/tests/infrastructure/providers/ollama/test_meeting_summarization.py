"""Tests for the Ollama meeting-summarization adapter."""

import asyncio
from typing import cast

import pytest
from app.application.dto.ai import LanguageCode, MeetingSummaryRequest
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.infrastructure.providers.ollama import (
    OllamaClient,
    OllamaMeetingSummarizationProvider,
)
from app.infrastructure.providers.ollama.meeting_summarization import (
    _build_meeting_summary_prompts,
)


class FakeOllamaClient:
    """Small fake that records structured generation calls."""

    def __init__(self, response: dict[str, object] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def generate_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        """Return the configured response after recording the request."""

        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": schema,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _request(
    *,
    include_action_items: bool = True,
    include_open_questions: bool = True,
) -> MeetingSummaryRequest:
    return MeetingSummaryRequest(
        meeting_name="Sprint planning",
        transcript_text="Mira confirms Friday. Alex will prepare the release notes.",
        language=LanguageCode(value="de-DE"),
        include_action_items=include_action_items,
        include_open_questions=include_open_questions,
    )


def _payload() -> dict[str, object]:
    return {
        "summary": "The team confirmed the Friday release.",
        "key_decisions": ["Release on Friday.", "Publish release notes."],
        "action_items": [
            {"text": "Prepare release notes.", "assignee": "Alex"},
            {"text": "Confirm deployment window.", "assignee": None},
        ],
        "open_questions": ["What is the deployment time?"],
    }


def _provider(
    response: dict[str, object] | Exception,
) -> tuple[OllamaMeetingSummarizationProvider, FakeOllamaClient]:
    client = FakeOllamaClient(response)
    return (
        OllamaMeetingSummarizationProvider(
            client=cast(OllamaClient, client),
            model="qwen2.5:3b",
        ),
        client,
    )


def test_prompts_represent_meeting_language_and_inclusion_flags() -> None:
    """Summary prompts contain request-owned meeting and inclusion preferences."""

    system_prompt, user_prompt = _build_meeting_summary_prompts(_request())

    assert "in de-de" in system_prompt.lower()
    assert "include action items" in system_prompt.lower()
    assert "include open questions" in system_prompt.lower()
    assert "Sprint planning" in user_prompt


def test_prompts_represent_disabled_optional_collections() -> None:
    """Disabled optional collections are explicitly disallowed in the prompt."""

    system_prompt, _ = _build_meeting_summary_prompts(
        _request(include_action_items=False, include_open_questions=False)
    )

    assert "use no action items" in system_prompt.lower()
    assert "use no open questions" in system_prompt.lower()


def test_summarize_forwards_schema_and_maps_valid_payload_in_order() -> None:
    """Valid payload fields preserve provider order and nullable assignees."""

    provider, client = _provider(_payload())

    result = asyncio.run(provider.summarize(_request()))

    assert result.summary == "The team confirmed the Friday release."
    assert result.key_decisions == ("Release on Friday.", "Publish release notes.")
    assert [item.text for item in result.action_items] == [
        "Prepare release notes.",
        "Confirm deployment window.",
    ]
    assert result.action_items[0].assignee == "Alex"
    assert result.action_items[1].assignee is None
    assert result.open_questions == ("What is the deployment time?",)
    call = client.calls[0]
    assert call["model"] == "qwen2.5:3b"
    schema = cast(dict[str, object], call["schema"])
    assert schema["required"] == [
        "summary",
        "key_decisions",
        "action_items",
        "open_questions",
    ]
    assert schema["additionalProperties"] is False


def test_summarize_forces_disabled_action_items_empty() -> None:
    """A disabled action-item collection is not exposed from provider output."""

    provider, _ = _provider(_payload())

    result = asyncio.run(provider.summarize(_request(include_action_items=False)))

    assert result.action_items == ()


def test_summarize_forces_disabled_open_questions_empty() -> None:
    """A disabled open-question collection is not exposed from provider output."""

    provider, _ = _provider(_payload())

    result = asyncio.run(provider.summarize(_request(include_open_questions=False)))

    assert result.open_questions == ()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "summary": "Summary",
            "key_decisions": [],
            "action_items": [],
            "open_questions": "not-a-list",
        },
        {
            "summary": " ",
            "key_decisions": [],
            "action_items": [],
            "open_questions": [],
        },
        {
            "summary": "Summary",
            "key_decisions": [" "],
            "action_items": [],
            "open_questions": [],
        },
        {
            "summary": "Summary",
            "key_decisions": [],
            "action_items": [],
            "open_questions": [" "],
        },
        {
            "summary": "Summary",
            "key_decisions": [],
            "action_items": [{"text": " ", "assignee": None}],
            "open_questions": [],
        },
        {
            "summary": "Summary",
            "key_decisions": [],
            "action_items": [{"text": "Task", "assignee": " "}],
            "open_questions": [],
        },
        {
            "summary": "Summary",
            "key_decisions": [],
            "action_items": [{"text": "Task", "assignee": 1}],
            "open_questions": [],
        },
    ],
)
def test_summarize_rejects_malformed_payload(payload: dict[str, object]) -> None:
    """Missing and malformed summary fields are invalid provider responses."""

    provider, _ = _provider(payload)

    with pytest.raises(InvalidProviderResponseError):
        asyncio.run(provider.summarize(_request()))


def test_summarize_propagates_provider_errors_unchanged() -> None:
    """Shared-client provider errors preserve their stable identity."""

    error = ProviderUnavailableError("Ollama is unavailable.")
    provider, _ = _provider(error)

    with pytest.raises(ProviderUnavailableError) as raised:
        asyncio.run(provider.summarize(_request()))

    assert raised.value is error
