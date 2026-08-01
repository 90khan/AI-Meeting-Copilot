"""Tests for the Ollama reply-coaching adapter."""

import asyncio
from typing import cast

import pytest
from app.application.dto.ai import (
    GermanLevel,
    LanguageCode,
    ReplyCoachingRequest,
    ReplyTone,
)
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.infrastructure.providers.ollama import (
    OllamaClient,
    OllamaReplyCoachingProvider,
)
from app.infrastructure.providers.ollama.reply_coaching import (
    _build_reply_coaching_prompts,
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
    target_german_level: GermanLevel | None = None,
    max_suggestions: int = 2,
) -> ReplyCoachingRequest:
    return ReplyCoachingRequest(
        conversation_context="We are discussing the deployment timeline.",
        latest_utterance="Can you confirm the release date?",
        response_language=LanguageCode(value="de"),
        target_german_level=target_german_level,
        tone=ReplyTone.CONFIDENT,
        max_suggestions=max_suggestions,
    )


def _provider(
    response: dict[str, object] | Exception,
) -> tuple[OllamaReplyCoachingProvider, FakeOllamaClient]:
    client = FakeOllamaClient(response)
    return (
        OllamaReplyCoachingProvider(
            client=cast(OllamaClient, client),
            model="qwen2.5:3b",
        ),
        client,
    )


def test_prompts_include_response_preferences_and_german_level() -> None:
    """A German-level request carries all relevant response requirements."""

    system_prompt, user_prompt = _build_reply_coaching_prompts(
        _request(target_german_level=GermanLevel.B1)
    )

    assert "use de" in system_prompt.lower()
    assert "confident tone" in system_prompt.lower()
    assert "no more than 2 suggestions" in system_prompt.lower()
    assert "cefr level b1" in system_prompt.lower()
    assert "short enough to say aloud" in system_prompt.lower()
    assert "deployment timeline" in user_prompt
    assert "release date" in user_prompt


def test_prompts_omit_german_level_instructions_when_not_requested() -> None:
    """No CEFR instruction is emitted unless the request supplies one."""

    system_prompt, _ = _build_reply_coaching_prompts(_request())

    assert "cefr" not in system_prompt.lower()
    assert "say aloud" not in system_prompt.lower()


def test_suggest_replies_forwards_dynamic_schema_and_maps_provider_order() -> None:
    """Valid provider items retain order and receive the locally requested tone."""

    provider, client = _provider(
        {
            "suggestions": [
                {"text": "Ja, der Termin ist bestätigt."},
                {"text": "Der Release ist am Freitag."},
            ]
        }
    )
    request = _request()

    result = asyncio.run(provider.suggest_replies(request))

    assert [suggestion.text for suggestion in result.suggestions] == [
        "Ja, der Termin ist bestätigt.",
        "Der Release ist am Freitag.",
    ]
    assert all(
        suggestion.tone is ReplyTone.CONFIDENT for suggestion in result.suggestions
    )
    call = client.calls[0]
    assert call["model"] == "qwen2.5:3b"
    schema = cast(dict[str, object], call["schema"])
    properties = cast(dict[str, object], schema["properties"])
    suggestions_schema = cast(dict[str, object], properties["suggestions"])
    assert suggestions_schema["minItems"] == 1
    assert suggestions_schema["maxItems"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"suggestions": "not-a-list"},
        {"suggestions": []},
        {"suggestions": [{"text": "One"}, {"text": "Two"}, {"text": "Three"}]},
        {"suggestions": ["not-an-object"]},
        {"suggestions": [{}]},
        {"suggestions": [{"text": 1}]},
        {"suggestions": [{"text": "  "}]},
    ],
)
def test_suggest_replies_rejects_malformed_payload(payload: dict[str, object]) -> None:
    """All malformed suggestion collections map to a stable response error."""

    provider, _ = _provider(payload)

    with pytest.raises(InvalidProviderResponseError):
        asyncio.run(provider.suggest_replies(_request()))


def test_suggest_replies_propagates_provider_errors_unchanged() -> None:
    """Shared-client provider errors preserve their stable identity."""

    error = ProviderUnavailableError("Ollama is unavailable.")
    provider, _ = _provider(error)

    with pytest.raises(ProviderUnavailableError) as raised:
        asyncio.run(provider.suggest_replies(_request()))

    assert raised.value is error
