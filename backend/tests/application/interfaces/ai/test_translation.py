"""Tests for the translation provider contract."""

import inspect

from app.application.dto.ai import LanguageCode, TranslationRequest, TranslationResult
from app.application.interfaces.ai import TranslationProvider


class FakeTranslationProvider:
    """Minimal structural implementation of the translation contract."""

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Return a predictable translation result for the supplied request."""

        return TranslationResult(
            translated_text=request.text,
            source_language=request.source_language or LanguageCode(value="de"),
            target_language=request.target_language,
        )


def test_translation_provider_method_is_asynchronous() -> None:
    """The provider contract exposes an asynchronous translation method."""

    assert inspect.iscoroutinefunction(TranslationProvider.translate)


def test_fake_provider_structurally_satisfies_the_protocol() -> None:
    """A translation adapter needs no nominal protocol inheritance."""

    provider: TranslationProvider = FakeTranslationProvider()

    assert isinstance(provider, TranslationProvider)
