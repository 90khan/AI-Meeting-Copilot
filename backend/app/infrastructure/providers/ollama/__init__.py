"""Ollama infrastructure components."""

from app.infrastructure.providers.ollama.client import OllamaClient
from app.infrastructure.providers.ollama.german_simplification import (
    OllamaGermanSimplificationProvider,
)
from app.infrastructure.providers.ollama.meeting_summarization import (
    OllamaMeetingSummarizationProvider,
)
from app.infrastructure.providers.ollama.reply_coaching import (
    OllamaReplyCoachingProvider,
)
from app.infrastructure.providers.ollama.translation import OllamaTranslationProvider

__all__ = [
    "OllamaClient",
    "OllamaGermanSimplificationProvider",
    "OllamaMeetingSummarizationProvider",
    "OllamaReplyCoachingProvider",
    "OllamaTranslationProvider",
]
