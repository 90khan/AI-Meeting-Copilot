"""Lazy lifecycle management for a local Faster-Whisper model."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from app.application.exceptions import ProviderUnavailableError

if TYPE_CHECKING:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]


type _ModelFactory = Callable[..., WhisperModel]


class FasterWhisperModelManager:
    """Lazily load and retain one Faster-Whisper model instance."""

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        cpu_threads: int | None,
        download_directory: Path | None,
        _model_factory: _ModelFactory | None = None,
    ) -> None:
        """Store model configuration without loading model weights."""

        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._download_directory = download_directory
        if _model_factory is None:
            self._model_factory: _ModelFactory = _create_whisper_model
        else:
            self._model_factory = _model_factory
        self._model: WhisperModel | None = None
        self._lock = Lock()

    def get_model(self) -> WhisperModel:
        """Return the cached model, loading it once when first requested."""

        with self._lock:
            if self._model is None:
                self._model = self._load_model()

            return self._model

    def close(self) -> None:
        """Release this manager's model reference without deleting model files."""

        with self._lock:
            self._model = None

    def _load_model(self) -> WhisperModel:
        """Construct a model and translate recoverable setup failures."""

        model_kwargs: dict[str, object] = {
            "device": self._device,
            "compute_type": self._compute_type,
        }
        if self._cpu_threads is not None:
            model_kwargs["cpu_threads"] = self._cpu_threads
        if self._download_directory is not None:
            model_kwargs["download_root"] = self._download_directory

        try:
            return self._model_factory(self._model_name, **model_kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except ProviderUnavailableError:
            raise
        except Exception as error:
            raise ProviderUnavailableError(
                "Faster-Whisper model could not be loaded."
            ) from error


def _create_whisper_model(
    model_name: str,
    *,
    device: str,
    compute_type: str,
    cpu_threads: int | None = None,
    download_root: Path | None = None,
) -> WhisperModel:
    """Construct a WhisperModel only when the model is first requested."""

    from faster_whisper import WhisperModel

    model_kwargs: dict[str, object] = {
        "device": device,
        "compute_type": compute_type,
    }
    if cpu_threads is not None:
        model_kwargs["cpu_threads"] = cpu_threads
    if download_root is not None:
        model_kwargs["download_root"] = str(download_root)

    return WhisperModel(model_name, **model_kwargs)
