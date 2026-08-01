"""Ollama adapter for the German simplification application capability."""

from app.application.dto.ai import (
    GermanSimplificationRequest,
    GermanSimplificationResult,
)
from app.application.exceptions import InvalidProviderResponseError
from app.infrastructure.providers.ollama.client import OllamaClient

_SIMPLIFICATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"simplified_text": {"type": "string"}},
    "required": ["simplified_text"],
}


class OllamaGermanSimplificationProvider:
    """Simplify German text through a local Ollama model."""

    def __init__(self, *, client: OllamaClient, model: str) -> None:
        """Initialize the adapter with its shared client and configured model."""

        self._client = client
        self._model = model

    async def simplify(
        self,
        request: GermanSimplificationRequest,
    ) -> GermanSimplificationResult:
        """Simplify a request and validate the capability-specific response."""

        system_prompt, user_prompt = _build_simplification_prompts(request)
        payload = await self._client.generate_structured(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=_SIMPLIFICATION_SCHEMA,
        )

        try:
            simplified_text = payload["simplified_text"]
            if not isinstance(simplified_text, str):
                raise TypeError("Simplified text must be a string.")
            return GermanSimplificationResult(
                original_text=request.text,
                simplified_text=simplified_text,
                target_level=request.target_level,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidProviderResponseError(
                "Ollama returned an invalid German simplification payload."
            ) from error


def _build_simplification_prompts(
    request: GermanSimplificationRequest,
) -> tuple[str, str]:
    """Build deterministic prompts for one German simplification request."""

    technical_terms_instruction = (
        "Preserve technical terms."
        if request.preserve_technical_terms
        else "Simplify technical wording where possible."
    )
    system_prompt = (
        "Rewrite only in German. Preserve the original meaning. "
        "Use the requested CEFR target level. Keep sentences concise and natural. "
        f"{technical_terms_instruction} "
        "Do not add explanations, commentary, Markdown, or headings. Output JSON only."
    )
    user_prompt = (
        "Simplify the following German text:\n"
        f"Target CEFR level: {request.target_level.value}\n"
        f"Preserve technical terms: {str(request.preserve_technical_terms).lower()}\n"
        "Text:\n"
        f"{request.text}"
    )
    return system_prompt, user_prompt
