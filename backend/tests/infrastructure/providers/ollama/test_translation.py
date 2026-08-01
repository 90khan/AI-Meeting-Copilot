"""Tests for the Ollama translation adapter."""

import asyncio
from typing import cast

import pytest
from app.application.dto.ai import LanguageCode, TranslationRequest
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.infrastructure.providers.ollama import OllamaClient, OllamaTranslationProvider
from app.infrastructure.providers.ollama.translation import _build_translation_prompts

_ENGLISH = LanguageCode(value="en")


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


def _request(*, source_language: LanguageCode | None = _ENGLISH) -> TranslationRequest:
    return TranslationRequest(
        text="Hello\nworld",
        source_language=source_language,
        target_language=LanguageCode(value="de"),
    )


def _provider(
    response: dict[str, object] | Exception,
) -> tuple[OllamaTranslationProvider, FakeOllamaClient]:
    client = FakeOllamaClient(response)
    return (
        OllamaTranslationProvider(
            client=cast(OllamaClient, client),
            model="qwen2.5:3b",
        ),
        client,
    )


def test_build_translation_prompts_instructs_faithful_json_translation() -> None:
    """The prompt directs the model to produce only faithful JSON translation."""

    system_prompt, user_prompt = _build_translation_prompts(_request())

    assert "faithfully" in system_prompt.lower()
    assert "preserve meaning" in system_prompt.lower()
    assert "preserve formatting" in system_prompt.lower()
    assert "do not explain" in system_prompt.lower()
    assert "json only" in system_prompt.lower()
    assert "never add markdown" in system_prompt.lower()
    assert "Source language: en" in user_prompt
    assert "Target language: de" in user_prompt
    assert "Preserve formatting: true" in user_prompt


def test_translate_invokes_client_with_translation_schema_and_maps_result() -> None:
    """The adapter sends schema-constrained prompts and returns application DTOs."""

    provider, client = _provider(
        {
            "translated_text": "Hallo\nWelt",
            "source_language": "en",
            "target_language": "de",
        }
    )

    result = asyncio.run(provider.translate(_request()))

    assert str(result.source_language) == "en"
    assert str(result.target_language) == "de"
    assert result.translated_text == "Hallo\nWelt"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "qwen2.5:3b"
    schema = cast(dict[str, object], call["schema"])
    assert schema["required"] == [
        "translated_text",
        "source_language",
        "target_language",
    ]
    assert schema["additionalProperties"] is False


def test_translate_accepts_detected_source_language_when_not_requested() -> None:
    """An omitted source language permits provider language detection."""

    provider, _ = _provider(
        {
            "translated_text": "Hello",
            "source_language": "de-DE",
            "target_language": "en",
        }
    )
    request = TranslationRequest(
        text="Hallo",
        source_language=None,
        target_language=LanguageCode(value="en"),
    )

    result = asyncio.run(provider.translate(request))

    assert str(result.source_language) == "de-de"


def test_translate_rejects_unexpected_requested_source_language() -> None:
    """A provider response must respect an explicit source-language request."""

    provider, _ = _provider(
        {
            "translated_text": "Hallo",
            "source_language": "fr",
            "target_language": "de",
        }
    )

    with pytest.raises(InvalidProviderResponseError, match="source language"):
        asyncio.run(provider.translate(_request()))


def test_translate_rejects_unexpected_target_language() -> None:
    """A provider response must respect the requested target language."""

    provider, _ = _provider(
        {
            "translated_text": "Hello",
            "source_language": "en",
            "target_language": "fr",
        }
    )

    with pytest.raises(InvalidProviderResponseError, match="target language"):
        asyncio.run(provider.translate(_request()))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"translated_text": "Hallo", "source_language": "en", "target_language": 1},
        {"translated_text": "", "source_language": "en", "target_language": "de"},
        {
            "translated_text": "Hallo",
            "source_language": "invalid!",
            "target_language": "de",
        },
    ],
)
def test_translate_rejects_malformed_payload(payload: dict[str, object]) -> None:
    """Malformed capability payloads are stable provider-response failures."""

    provider, _ = _provider(payload)

    with pytest.raises(InvalidProviderResponseError):
        asyncio.run(provider.translate(_request()))


def test_translate_propagates_provider_errors_unchanged() -> None:
    """Shared-client provider failures keep their stable error type and identity."""

    error = ProviderUnavailableError("Ollama is unavailable.")
    provider, _ = _provider(error)

    with pytest.raises(ProviderUnavailableError) as raised:
        asyncio.run(provider.translate(_request()))

    assert raised.value is error
