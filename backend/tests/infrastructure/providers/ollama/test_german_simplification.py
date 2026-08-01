"""Tests for the Ollama German simplification adapter."""

import asyncio
from typing import cast

import pytest
from app.application.dto.ai import GermanLevel, GermanSimplificationRequest
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.infrastructure.providers.ollama import (
    OllamaClient,
    OllamaGermanSimplificationProvider,
)
from app.infrastructure.providers.ollama.german_simplification import (
    _build_simplification_prompts,
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
    preserve_technical_terms: bool = True,
) -> GermanSimplificationRequest:
    return GermanSimplificationRequest(
        text="Die Anwendung verwendet eine komplexe Datenbankkonfiguration.",
        target_level=GermanLevel.B1,
        preserve_technical_terms=preserve_technical_terms,
    )


def _provider(
    response: dict[str, object] | Exception,
) -> tuple[OllamaGermanSimplificationProvider, FakeOllamaClient]:
    client = FakeOllamaClient(response)
    return (
        OllamaGermanSimplificationProvider(
            client=cast(OllamaClient, client),
            model="qwen2.5:3b",
        ),
        client,
    )


def test_build_simplification_prompts_represent_requested_level_and_terms() -> None:
    """Prompts direct German-only simplification at the requested CEFR level."""

    system_prompt, user_prompt = _build_simplification_prompts(_request())

    assert "only in german" in system_prompt.lower()
    assert "preserve the original meaning" in system_prompt.lower()
    assert "concise and natural" in system_prompt.lower()
    assert "preserve technical terms" in system_prompt.lower()
    assert "json only" in system_prompt.lower()
    assert "Target CEFR level: b1" in user_prompt
    assert "Preserve technical terms: true" in user_prompt


def test_build_simplification_prompts_can_simplify_technical_wording() -> None:
    """The opt-out is represented directly in the prompt."""

    system_prompt, user_prompt = _build_simplification_prompts(
        _request(preserve_technical_terms=False)
    )

    assert "simplify technical wording where possible" in system_prompt.lower()
    assert "Preserve technical terms: false" in user_prompt


def test_simplify_forwards_schema_and_maps_valid_payload() -> None:
    """The adapter calls the shared client and preserves request-owned fields."""

    provider, client = _provider(
        {"simplified_text": "Die App nutzt eine schwierige Datenbank-Einstellung."}
    )
    request = _request()

    result = asyncio.run(provider.simplify(request))

    assert result.original_text == request.text
    assert result.target_level is GermanLevel.B1
    assert result.simplified_text == (
        "Die App nutzt eine schwierige Datenbank-Einstellung."
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "qwen2.5:3b"
    assert cast(dict[str, object], call["schema"]) == {
        "type": "object",
        "properties": {"simplified_text": {"type": "string"}},
        "required": ["simplified_text"],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"simplified_text": 1},
        {"simplified_text": "   "},
    ],
)
def test_simplify_rejects_malformed_payload(payload: dict[str, object]) -> None:
    """Missing, non-text, and blank results are invalid provider responses."""

    provider, _ = _provider(payload)

    with pytest.raises(InvalidProviderResponseError):
        asyncio.run(provider.simplify(_request()))


def test_simplify_propagates_provider_errors_unchanged() -> None:
    """The shared client's stable provider errors retain their identity."""

    error = ProviderUnavailableError("Ollama is unavailable.")
    provider, _ = _provider(error)

    with pytest.raises(ProviderUnavailableError) as raised:
        asyncio.run(provider.simplify(_request()))

    assert raised.value is error
