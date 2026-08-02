"""Privacy-safe errors for the recording key-store boundary."""

from typing import ClassVar


class RecordingKeyStoreError(Exception):
    """Base error for local recording-key operations."""

    DEFAULT_MESSAGE: ClassVar[str] = "Recording key storage is unavailable."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.DEFAULT_MESSAGE)


class RecordingKeyUnavailableError(RecordingKeyStoreError):
    DEFAULT_MESSAGE = "Recording key storage is unavailable."


class RecordingKeyNotFoundError(RecordingKeyStoreError):
    DEFAULT_MESSAGE = "The recording key is unavailable."


class RecordingKeyAccessDeniedError(RecordingKeyStoreError):
    DEFAULT_MESSAGE = "Recording key access was denied."


class RecordingKeyInvalidDataError(RecordingKeyStoreError):
    DEFAULT_MESSAGE = "Recording key data is invalid."
