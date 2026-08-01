"""Ollama infrastructure components."""

from app.infrastructure.providers.ollama.client import OllamaClient
from app.infrastructure.providers.ollama.german_simplification import (
    OllamaGermanSimplificationProvider,
)
from app.infrastructure.providers.ollama.translation import OllamaTranslationProvider

__all__ = [
    "OllamaClient",
    "OllamaGermanSimplificationProvider",
    "OllamaTranslationProvider",
]
