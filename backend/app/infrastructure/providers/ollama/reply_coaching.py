"""Ollama adapter for the reply-coaching application capability."""

from app.application.dto.ai import (
    ReplyCoachingRequest,
    ReplyCoachingResult,
    ReplySuggestion,
)
from app.application.exceptions import InvalidProviderResponseError
from app.infrastructure.providers.ollama.client import OllamaClient


class OllamaReplyCoachingProvider:
    """Generate reply suggestions through a local Ollama model."""

    def __init__(self, *, client: OllamaClient, model: str) -> None:
        """Initialize the adapter with its shared client and configured model."""

        self._client = client
        self._model = model

    async def suggest_replies(
        self,
        request: ReplyCoachingRequest,
    ) -> ReplyCoachingResult:
        """Generate and validate concise replies for one conversation."""

        system_prompt, user_prompt = _build_reply_coaching_prompts(request)
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "suggestions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": request.max_suggestions,
                    "items": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["suggestions"],
            "additionalProperties": False,
        }
        payload = await self._client.generate_structured(
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
        )

        try:
            suggestions_payload = payload["suggestions"]
            if not isinstance(suggestions_payload, list):
                raise TypeError("Suggestions must be a list.")
            if not suggestions_payload:
                raise ValueError("Suggestions must not be empty.")
            if len(suggestions_payload) > request.max_suggestions:
                raise ValueError("Suggestions exceed the requested maximum.")

            suggestions: list[ReplySuggestion] = []
            for item in suggestions_payload:
                if not isinstance(item, dict):
                    raise TypeError("Each suggestion must be an object.")
                text = item["text"]
                if not isinstance(text, str):
                    raise TypeError("Suggestion text must be a string.")
                suggestions.append(ReplySuggestion(text=text, tone=request.tone))
            return ReplyCoachingResult(suggestions=tuple(suggestions))
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidProviderResponseError(
                "Ollama returned an invalid reply coaching payload."
            ) from error


def _build_reply_coaching_prompts(
    request: ReplyCoachingRequest,
) -> tuple[str, str]:
    """Build deterministic prompts for one reply-coaching request."""

    german_level_instruction = (
        "Produce natural German at CEFR level "
        f"{request.target_german_level.value} and keep replies short enough "
        "to say aloud."
        if request.target_german_level is not None
        else ""
    )
    system_prompt = (
        "Generate concise, immediately usable reply suggestions. "
        f"Use {request.response_language} and a {request.tone.value} tone. "
        f"Return no more than {request.max_suggestions} suggestions. "
        f"{german_level_instruction} "
        "Do not explain the suggestions. Do not add Markdown, headings, numbering, "
        "or commentary. Output JSON only."
    )
    user_prompt = (
        "Conversation context:\n"
        f"{request.conversation_context}\n\n"
        "Latest utterance:\n"
        f"{request.latest_utterance}"
    )
    return system_prompt, user_prompt
