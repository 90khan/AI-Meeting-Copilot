"""Tests for AI provider factory boundaries in the application container."""

import asyncio

import pytest
from app.application.dto.ai import (
    LanguageCode,
    TranslationRequest,
    TranslationResult,
)
from app.application.exceptions import ProviderUnavailableError
from app.application.interfaces import TranslationProvider
from app.core.config import Settings
from app.core.container import Container


class FakeTranslationProvider:
    """Minimal translation provider used to exercise container wiring."""

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Return a predictable translation result."""

        return TranslationResult(
            translated_text=request.text,
            source_language=request.source_language or LanguageCode(value="de"),
            target_language=request.target_language,
        )


def test_missing_provider_registration_raises_provider_unavailable_error() -> None:
    """Every unresolved AI capability fails at the stable provider boundary."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    for resolver in (
        container.get_speech_to_text_provider,
        container.get_translation_provider,
        container.get_german_simplification_provider,
        container.get_reply_coaching_provider,
        container.get_meeting_summarization_provider,
    ):
        with pytest.raises(ProviderUnavailableError, match="not configured"):
            resolver()


def test_registered_provider_factory_resolves_a_new_provider_per_call() -> None:
    """Container resolution delegates to the registered factory without caching."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    created_providers: list[FakeTranslationProvider] = []

    def factory() -> TranslationProvider:
        provider = FakeTranslationProvider()
        created_providers.append(provider)
        return provider

    container.register_translation_provider_factory(factory)

    first_provider = container.get_translation_provider()
    second_provider = container.get_translation_provider()

    assert isinstance(first_provider, FakeTranslationProvider)
    assert isinstance(second_provider, FakeTranslationProvider)
    assert first_provider is not second_provider
    assert len(created_providers) == 2


def test_provider_registration_is_sealed_while_container_is_started() -> None:
    """Provider factories cannot change while lifecycle-managed resources are active."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    container.register_translation_provider_factory(FakeTranslationProvider)
    asyncio.run(container.start())

    try:
        with pytest.raises(RuntimeError, match="cannot change after container start"):
            container.register_translation_provider_factory(FakeTranslationProvider)
    finally:
        asyncio.run(container.stop())


def test_provider_registration_does_not_change_persistence_lifecycle() -> None:
    """AI factory registration leaves persistence resource ownership unchanged."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    container.register_translation_provider_factory(FakeTranslationProvider)

    assert container._engine is None
    assert container._session_factory is None

    asyncio.run(container.start())
    try:
        assert container._engine is not None
        assert container._session_factory is not None
    finally:
        asyncio.run(container.stop())
