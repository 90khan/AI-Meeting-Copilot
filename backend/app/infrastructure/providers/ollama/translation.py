"""Ollama adapter for the translation application capability."""

from app.application.dto.ai import (
    LanguageCode,
    TranslationRequest,
    TranslationResult,
)
from app.application.exceptions import InvalidProviderResponseError
from app.core.throughput_diagnostics import emit_throughput
from app.infrastructure.providers.ollama.client import OllamaClient

_TRANSLATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "translated_text": {"type": "string"},
        "source_language": {"type": "string"},
        "target_language": {"type": "string"},
    },
    "required": ["translated_text", "source_language", "target_language"],
    "additionalProperties": False,
}


class OllamaTranslationProvider:
    """Translate text through a local Ollama model."""

    def __init__(self, *, client: OllamaClient, model: str) -> None:
        """Initialize the adapter with its shared client and configured model."""

        self._client = client
        self._model = model

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate a request and validate the capability-specific response."""

        system_prompt, user_prompt = _build_translation_prompts(request)
        payload = await self._client.generate_structured(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=_TRANSLATION_SCHEMA,
        )
        emit_throughput("assist_translation_response_received")

        try:
            translated_text = payload["translated_text"]
            source_language = payload["source_language"]
            target_language = payload["target_language"]
            if not isinstance(translated_text, str):
                raise TypeError("Translation fields must be strings.")
            if not isinstance(source_language, str):
                raise TypeError("Translation fields must be strings.")
            if not isinstance(target_language, str):
                raise TypeError("Translation fields must be strings.")
            result = TranslationResult(
                translated_text=translated_text,
                source_language=LanguageCode(value=source_language),
                target_language=LanguageCode(value=target_language),
            )
        except (KeyError, TypeError, ValueError) as error:
            emit_throughput(
                "assist_translation_response_validation_failed",
                reason="response_validation_failed",
            )
            raise InvalidProviderResponseError(
                "Ollama returned an invalid translation payload."
            ) from error

        if result.target_language != request.target_language:
            emit_throughput(
                "assist_translation_response_validation_failed",
                reason="response_validation_failed",
            )
            raise InvalidProviderResponseError(
                "Ollama returned an unexpected target language."
            )
        if (
            request.source_language is not None
            and result.source_language != request.source_language
        ):
            emit_throughput(
                "assist_translation_response_validation_failed",
                reason="response_validation_failed",
            )
            raise InvalidProviderResponseError(
                "Ollama returned an unexpected source language."
            )

        return result


def _build_translation_prompts(request: TranslationRequest) -> tuple[str, str]:
    """Build deterministic prompts for one schema-constrained translation request."""

    source_language = (
        str(request.source_language)
        if request.source_language is not None
        else "auto-detect"
    )
    system_prompt = (
        "Translate faithfully. Preserve meaning and preserve formatting "
        "when requested. "
        "Do not explain the translation. Output JSON only. Never add Markdown."
    )
    user_prompt = (
        "Translate this text with the following requirements:\n"
        f"Source language: {source_language}\n"
        f"Target language: {request.target_language}\n"
        f"Preserve formatting: {str(request.preserve_formatting).lower()}\n"
        "Text:\n"
        f"{request.text}"
    )
    return system_prompt, user_prompt
