"""Tests for Ollama capability wiring in the application container."""

import asyncio
from typing import ClassVar

import app.core.container as container_module
import pytest
from app.application.exceptions import ProviderUnavailableError
from app.core.config import Settings
from app.core.container import Container


class FakeOllamaClient:
    """Non-networking shared-client double with lifecycle call tracking."""

    instances: ClassVar[list["FakeOllamaClient"]] = []

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        temperature: float,
        context_length: int,
        keep_alive: str,
    ) -> None:
        """Record construction without contacting an Ollama daemon."""

        self.arguments = {
            "base_url": base_url,
            "request_timeout_seconds": request_timeout_seconds,
            "temperature": temperature,
            "context_length": context_length,
            "keep_alive": keep_alive,
        }
        self.close_calls = 0
        self.network_calls = 0
        self.instances.append(self)

    async def close(self) -> None:
        """Record lifecycle closure."""

        self.close_calls += 1


class FakeTranslationProvider:
    """Translation-adapter double that records its model and shared client."""

    instances: ClassVar[list["FakeTranslationProvider"]] = []

    def __init__(self, *, client: FakeOllamaClient, model: str) -> None:
        self.client = client
        self.model = model
        self.instances.append(self)


class FakeGermanSimplificationProvider:
    """German-simplification adapter double."""

    instances: ClassVar[list["FakeGermanSimplificationProvider"]] = []

    def __init__(self, *, client: FakeOllamaClient, model: str) -> None:
        self.client = client
        self.model = model
        self.instances.append(self)


class FakeReplyCoachingProvider:
    """Reply-coaching adapter double."""

    instances: ClassVar[list["FakeReplyCoachingProvider"]] = []

    def __init__(self, *, client: FakeOllamaClient, model: str) -> None:
        self.client = client
        self.model = model
        self.instances.append(self)


class FakeMeetingSummarizationProvider:
    """Meeting-summarization adapter double."""

    instances: ClassVar[list["FakeMeetingSummarizationProvider"]] = []

    def __init__(self, *, client: FakeOllamaClient, model: str) -> None:
        self.client = client
        self.model = model
        self.instances.append(self)


def _reset_fakes() -> None:
    """Clear class-level records between tests."""

    FakeOllamaClient.instances.clear()
    FakeTranslationProvider.instances.clear()
    FakeGermanSimplificationProvider.instances.clear()
    FakeReplyCoachingProvider.instances.clear()
    FakeMeetingSummarizationProvider.instances.clear()


def _configure_ollama_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace Ollama infrastructure classes with non-networking doubles."""

    _reset_fakes()
    monkeypatch.setattr(container_module, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(
        container_module,
        "OllamaTranslationProvider",
        FakeTranslationProvider,
    )
    monkeypatch.setattr(
        container_module,
        "OllamaGermanSimplificationProvider",
        FakeGermanSimplificationProvider,
    )
    monkeypatch.setattr(
        container_module,
        "OllamaReplyCoachingProvider",
        FakeReplyCoachingProvider,
    )
    monkeypatch.setattr(
        container_module,
        "OllamaMeetingSummarizationProvider",
        FakeMeetingSummarizationProvider,
    )


def _settings(**overrides: object) -> Settings:
    """Create container settings with an isolated in-memory database."""

    values: dict[str, object] = {"database_url": "sqlite+pysqlite:///:memory:"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_unconfigured_ollama_capabilities_create_no_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No client is created when no capability selects Ollama."""

    _configure_ollama_fakes(monkeypatch)
    container = Container(_settings())

    asyncio.run(container.start())
    try:
        assert FakeOllamaClient.instances == []
        with pytest.raises(ProviderUnavailableError, match="Translation provider"):
            container.get_translation_provider()
    finally:
        asyncio.run(container.stop())


def test_one_ollama_capability_uses_one_cached_provider_and_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One selected capability creates one client and one lifecycle provider."""

    _configure_ollama_fakes(monkeypatch)
    container = Container(_settings(translation_provider="ollama"))

    asyncio.run(container.start())
    try:
        assert len(FakeOllamaClient.instances) == 1
        provider = container.get_translation_provider()
        assert provider is container.get_translation_provider()
        assert provider is FakeTranslationProvider.instances[0]
        assert FakeTranslationProvider.instances[0].model == "qwen2.5:3b"
        assert FakeOllamaClient.instances[0].network_calls == 0
    finally:
        asyncio.run(container.stop())


def test_all_ollama_capabilities_share_client_and_use_capability_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All selected adapters share one client and receive their own model setting."""

    _configure_ollama_fakes(monkeypatch)
    container = Container(
        _settings(
            translation_provider="ollama",
            german_simplification_provider="ollama",
            reply_coaching_provider="ollama",
            meeting_summarization_provider="ollama",
            ollama_translation_model="translation-model",
            ollama_german_simplification_model="simplification-model",
            ollama_reply_coaching_model="coaching-model",
            ollama_meeting_summarization_model="summary-model",
        )
    )

    asyncio.run(container.start())
    try:
        client = FakeOllamaClient.instances[0]
        assert len(FakeOllamaClient.instances) == 1
        assert FakeTranslationProvider.instances[0].client is client
        assert FakeTranslationProvider.instances[0].model == "translation-model"
        assert FakeGermanSimplificationProvider.instances[0].model == (
            "simplification-model"
        )
        assert FakeReplyCoachingProvider.instances[0].model == "coaching-model"
        assert FakeMeetingSummarizationProvider.instances[0].model == "summary-model"
    finally:
        asyncio.run(container.stop())


def test_manual_factory_overrides_one_automatic_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual registration takes precedence while another capability stays automatic."""

    _configure_ollama_fakes(monkeypatch)
    container = Container(
        _settings(
            translation_provider="ollama",
            german_simplification_provider="ollama",
        )
    )
    manual_providers = [object(), object()]

    def factory() -> object:
        return manual_providers.pop(0)

    container.register_translation_provider_factory(factory)  # type: ignore[arg-type]
    asyncio.run(container.start())
    try:
        assert FakeTranslationProvider.instances == []
        assert len(FakeOllamaClient.instances) == 1
        first_provider = container.get_translation_provider()
        second_provider = container.get_translation_provider()
        assert first_provider is not second_provider
        assert container.get_german_simplification_provider() is (
            FakeGermanSimplificationProvider.instances[0]
        )
    finally:
        asyncio.run(container.stop())


def test_unsupported_ollama_capability_name_fails_and_cleans_resources() -> None:
    """Unsupported provider names identify their capability during startup."""

    container = Container(_settings(reply_coaching_provider="unsupported"))

    with pytest.raises(
        ProviderUnavailableError,
        match="Unsupported reply coaching provider: unsupported",
    ):
        asyncio.run(container.start())

    assert container._engine is None
    assert container._session_factory is None
    assert container._ollama_client is None


def test_ollama_start_stop_and_resolvers_obey_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start is idempotent, stop closes once, and resolvers require lifecycle state."""

    _configure_ollama_fakes(monkeypatch)
    container = Container(_settings(translation_provider="ollama"))

    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_translation_provider()

    asyncio.run(container.start())
    client = FakeOllamaClient.instances[0]
    asyncio.run(container.start())
    asyncio.run(container.stop())
    asyncio.run(container.stop())

    assert len(FakeOllamaClient.instances) == 1
    assert client.close_calls == 1
    assert container._ollama_client is None
    assert container._ollama_translation_provider is None
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_translation_provider()


def test_partial_ollama_startup_failure_closes_client_and_clears_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter-construction failure cleans up the newly created shared client."""

    _configure_ollama_fakes(monkeypatch)

    class FailingTranslationProvider:
        """Adapter double that fails during lifecycle construction."""

        def __init__(self, *, client: FakeOllamaClient, model: str) -> None:
            raise RuntimeError("adapter creation failed")

    monkeypatch.setattr(
        container_module,
        "OllamaTranslationProvider",
        FailingTranslationProvider,
    )
    container = Container(_settings(translation_provider="ollama"))

    with pytest.raises(RuntimeError, match="adapter creation failed"):
        asyncio.run(container.start())

    assert FakeOllamaClient.instances[0].close_calls == 1
    assert container._ollama_client is None
    assert container._ollama_translation_provider is None
    assert container._engine is None
    assert container._session_factory is None
