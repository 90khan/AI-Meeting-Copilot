"""Privacy-safe local recording storage errors."""

from typing import ClassVar


class RecordingStorageError(Exception):
    DEFAULT_MESSAGE: ClassVar[str] = "Recording storage operation failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.DEFAULT_MESSAGE)


class RecordingStorageUnavailableError(RecordingStorageError):
    DEFAULT_MESSAGE = "Recording storage is unavailable."


class RecordingSegmentTooLargeError(RecordingStorageError):
    DEFAULT_MESSAGE = "Recording segment exceeds the configured limit."


class RecordingSegmentNotFoundError(RecordingStorageError):
    DEFAULT_MESSAGE = "Recording segment is unavailable."


class RecordingSegmentCorruptError(RecordingStorageError):
    DEFAULT_MESSAGE = "Recording segment data is invalid."


class RecordingSegmentAuthenticationError(RecordingStorageError):
    DEFAULT_MESSAGE = "Recording segment authentication failed."


class RecordingSegmentLifecycleError(RecordingStorageError):
    DEFAULT_MESSAGE = "Recording segment lifecycle is invalid."
