"""Tests for Faster-Whisper container lifecycle wiring."""

import asyncio
from pathlib import Path
from typing import ClassVar

import app.core.container as container_module
import pytest
from app.application.exceptions import ProviderUnavailableError
from app.core.config import Settings
from app.core.container import Container


class FakeModelManager:
    """Model-manager double that never loads a real model."""

    instances: ClassVar[list["FakeModelManager"]] = []

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        cpu_threads: int | None,
        download_directory: Path | None,
    ) -> None:
        """Record configured construction arguments."""

        self.arguments = {
            "model_name": model_name,
            "device": device,
            "compute_type": compute_type,
            "cpu_threads": cpu_threads,
            "download_directory": download_directory,
        }
        self.get_model_calls = 0
        self.close_calls = 0
        self.instances.append(self)

    def get_model(self) -> object:
        """Record unexpected model loading attempts."""

        self.get_model_calls += 1
        return object()

    def close(self) -> None:
        """Record lifecycle cleanup."""

        self.close_calls += 1


class FakeSpeechToTextProvider:
    """Speech-provider double that records its constructor dependencies."""

    instances: ClassVar[list["FakeSpeechToTextProvider"]] = []

    def __init__(
        self,
        *,
        model_manager: FakeModelManager,
        beam_size: int,
        vad_enabled: bool,
    ) -> None:
        """Record configured provider arguments."""

        self.model_manager = model_manager
        self.beam_size = beam_size
        self.vad_enabled = vad_enabled
        self.instances.append(self)


def reset_fakes() -> None:
    """Clear class-level fake-instance records between tests."""

    FakeModelManager.instances.clear()
    FakeSpeechToTextProvider.instances.clear()


def configure_faster_whisper_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace concrete lifecycle classes with non-loading test doubles."""

    reset_fakes()
    monkeypatch.setattr(container_module, "FasterWhisperModelManager", FakeModelManager)
    monkeypatch.setattr(
        container_module,
        "FasterWhisperSpeechToTextProvider",
        FakeSpeechToTextProvider,
    )


def make_settings(**overrides: object) -> Settings:
    """Create Faster-Whisper settings for container tests."""

    values: dict[str, object] = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "speech_to_text_provider": "faster-whisper",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_faster_whisper_is_created_lazily_for_one_container_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup wires one provider and manager without loading model weights."""

    configure_faster_whisper_fakes(monkeypatch)
    container = Container(
        make_settings(
            faster_whisper_cpu_threads=4,
            faster_whisper_download_directory=Path("/tmp/models"),
            faster_whisper_beam_size=7,
            faster_whisper_vad_enabled=True,
        )
    )

    assert FakeModelManager.instances == []
    assert FakeSpeechToTextProvider.instances == []
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_speech_to_text_provider()

    asyncio.run(container.start())

    manager = FakeModelManager.instances[0]
    provider = FakeSpeechToTextProvider.instances[0]
    assert manager.arguments == {
        "model_name": "small",
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 4,
        "download_directory": Path("/tmp/models").resolve(),
    }
    assert provider.model_manager is manager
    assert provider.beam_size == 7
    assert provider.vad_enabled is True
    assert manager.get_model_calls == 0
    assert container.get_speech_to_text_provider() is provider
    assert container.get_speech_to_text_provider() is provider
    assert container._engine is not None
    assert container._session_factory is not None

    asyncio.run(container.stop())


def test_repeated_start_reuses_the_auto_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting twice preserves one manager and one provider instance."""

    configure_faster_whisper_fakes(monkeypatch)
    container = Container(make_settings())

    asyncio.run(container.start())
    provider = container.get_speech_to_text_provider()
    engine = container._engine
    asyncio.run(container.start())

    assert len(FakeModelManager.instances) == 1
    assert len(FakeSpeechToTextProvider.instances) == 1
    assert container.get_speech_to_text_provider() is provider
    assert container._engine is engine

    asyncio.run(container.stop())


def test_stop_closes_auto_manager_and_clears_speech_lifecycle_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping cleans up manager/provider state and remains idempotent."""

    configure_faster_whisper_fakes(monkeypatch)
    container = Container(make_settings())
    asyncio.run(container.start())
    manager = FakeModelManager.instances[0]

    asyncio.run(container.stop())
    asyncio.run(container.stop())

    assert manager.close_calls == 1
    assert container._faster_whisper_model_manager is None
    assert container._faster_whisper_speech_to_text_provider is None
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_speech_to_text_provider()


def test_unconfigured_speech_provider_remains_unavailable_after_start() -> None:
    """An unconfigured setting creates no automatic speech provider."""

    container = Container(make_settings(speech_to_text_provider="unconfigured"))
    asyncio.run(container.start())

    try:
        with pytest.raises(ProviderUnavailableError, match="not configured"):
            container.get_speech_to_text_provider()
    finally:
        asyncio.run(container.stop())


def test_unsupported_speech_provider_fails_clearly_during_startup() -> None:
    """Unsupported provider names fail before the container begins serving."""

    container = Container(make_settings(speech_to_text_provider="unknown"))

    with pytest.raises(
        ProviderUnavailableError,
        match="Unsupported speech-to-text provider",
    ):
        asyncio.run(container.start())

    assert container._engine is None
    assert container._session_factory is None


def test_manual_speech_factory_takes_precedence_over_automatic_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit manual registration bypasses the settings-selected provider."""

    configure_faster_whisper_fakes(monkeypatch)
    container = Container(make_settings())
    providers = [object(), object()]

    def factory() -> object:
        return providers.pop(0)

    container.register_speech_to_text_provider_factory(factory)  # type: ignore[arg-type]
    asyncio.run(container.start())

    try:
        assert FakeModelManager.instances == []
        first_provider = container.get_speech_to_text_provider()
        second_provider = container.get_speech_to_text_provider()

        assert first_provider is not second_provider
    finally:
        asyncio.run(container.stop())
