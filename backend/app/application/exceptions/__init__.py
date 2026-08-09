"""Application-layer exception types."""

from app.application.exceptions.provider_errors import (
    InvalidProviderResponseError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.application.exceptions.recording_key_errors import (
    RecordingKeyAccessDeniedError,
    RecordingKeyInvalidDataError,
    RecordingKeyNotFoundError,
    RecordingKeyStoreError,
    RecordingKeyUnavailableError,
)
from app.application.exceptions.recording_storage_errors import (
    RecordingPlaybackUnavailableError,
    RecordingSegmentAuthenticationError,
    RecordingSegmentCorruptError,
    RecordingSegmentLifecycleError,
    RecordingSegmentNotFoundError,
    RecordingSegmentTooLargeError,
    RecordingStorageError,
    RecordingStorageUnavailableError,
)
from app.application.exceptions.validation import ApplicationValidationError

__all__ = [
    "ApplicationValidationError",
    "InvalidProviderResponseError",
    "ProviderAuthenticationError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RecordingKeyAccessDeniedError",
    "RecordingKeyInvalidDataError",
    "RecordingKeyNotFoundError",
    "RecordingKeyStoreError",
    "RecordingKeyUnavailableError",
    "RecordingPlaybackUnavailableError",
    "RecordingSegmentAuthenticationError",
    "RecordingSegmentCorruptError",
    "RecordingSegmentLifecycleError",
    "RecordingSegmentNotFoundError",
    "RecordingSegmentTooLargeError",
    "RecordingStorageError",
    "RecordingStorageUnavailableError",
]
