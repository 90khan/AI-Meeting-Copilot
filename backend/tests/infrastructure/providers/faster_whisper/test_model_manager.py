"""Tests for lazy Faster-Whisper model lifecycle management."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import sleep

import pytest
from app.application.exceptions import ProviderUnavailableError
from app.infrastructure.providers.faster_whisper import FasterWhisperModelManager


class FakeWhisperModel:
    """Minimal stand-in for a Faster-Whisper model."""


def make_manager(
    factory: object,
    *,
    cpu_threads: int | None = None,
    download_directory: Path | None = None,
) -> FasterWhisperModelManager:
    """Create a model manager with a controllable internal model factory."""

    return FasterWhisperModelManager(
        model_name="small",
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        download_directory=download_directory,
        _model_factory=factory,  # type: ignore[arg-type]
    )


def test_construction_does_not_load_a_model() -> None:
    """Manager construction retains only configuration."""

    calls: list[object] = []

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        calls.append((args, kwargs))
        return FakeWhisperModel()

    make_manager(factory)

    assert calls == []


def test_first_get_model_loads_once_and_reuses_the_model() -> None:
    """The first model request loads once and all later calls share it."""

    calls: list[object] = []
    model = FakeWhisperModel()

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        calls.append((args, kwargs))
        return model

    manager = make_manager(factory)

    assert manager.get_model() is model
    assert manager.get_model() is model
    assert len(calls) == 1


def test_concurrent_get_model_calls_load_once() -> None:
    """The loading lock prevents duplicate model construction."""

    calls: list[object] = []
    model = FakeWhisperModel()

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        calls.append((args, kwargs))
        sleep(0.01)
        return model

    manager = make_manager(factory)

    with ThreadPoolExecutor(max_workers=8) as executor:
        models = list(executor.map(lambda _: manager.get_model(), range(8)))

    assert models == [model] * 8
    assert len(calls) == 1


def test_optional_model_constructor_arguments_are_forwarded() -> None:
    """Configured optional runtime parameters reach the model factory."""

    captured_arguments: dict[str, object] = {}
    download_directory = Path("/tmp/whisper-models")

    def factory(model_name: str, **kwargs: object) -> FakeWhisperModel:
        captured_arguments["model_name"] = model_name
        captured_arguments.update(kwargs)
        return FakeWhisperModel()

    manager = make_manager(
        factory,
        cpu_threads=4,
        download_directory=download_directory,
    )

    manager.get_model()

    assert captured_arguments == {
        "model_name": "small",
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 4,
        "download_root": download_directory,
    }


def test_absent_optional_arguments_are_not_forwarded() -> None:
    """Unset optional runtime parameters are omitted from model construction."""

    captured_arguments: dict[str, object] = {}

    def factory(model_name: str, **kwargs: object) -> FakeWhisperModel:
        captured_arguments["model_name"] = model_name
        captured_arguments.update(kwargs)
        return FakeWhisperModel()

    make_manager(factory).get_model()

    assert captured_arguments == {
        "model_name": "small",
        "device": "cpu",
        "compute_type": "int8",
    }


def test_close_clears_cached_model_and_is_idempotent() -> None:
    """Closing releases the cache and can be safely repeated."""

    models = [FakeWhisperModel(), FakeWhisperModel()]

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        return models.pop(0)

    manager = make_manager(factory)
    first_model = manager.get_model()

    manager.close()
    manager.close()
    second_model = manager.get_model()

    assert first_model is not second_model


def test_load_failure_becomes_provider_unavailable_error() -> None:
    """Recoverable model construction failures use the stable provider error."""

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        raise RuntimeError("model unavailable")

    with pytest.raises(ProviderUnavailableError, match="could not be loaded"):
        make_manager(factory).get_model()


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit()])
def test_process_control_exceptions_propagate_unchanged(error: BaseException) -> None:
    """Process-control exceptions are not translated into provider errors."""

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        raise error

    with pytest.raises(type(error)):
        make_manager(factory).get_model()
