"""Stable application exceptions for AI provider failures."""

from typing import ClassVar


class ProviderError(Exception):
    """Base exception for failures at an AI provider boundary."""

    DEFAULT_MESSAGE: ClassVar[str] = "An AI provider error occurred."

    def __init__(self, message: str | None = None) -> None:
        """Initialize the error with a custom or default message."""

        super().__init__(message if message is not None else self.DEFAULT_MESSAGE)


class ProviderUnavailableError(ProviderError):
    """Raised when an AI provider cannot be reached or used."""

    DEFAULT_MESSAGE: ClassVar[str] = "The AI provider is unavailable."


class ProviderTimeoutError(ProviderError):
    """Raised when an AI provider does not respond before its deadline."""

    DEFAULT_MESSAGE: ClassVar[str] = "The AI provider request timed out."


class ProviderAuthenticationError(ProviderError):
    """Raised when AI provider authentication fails."""

    DEFAULT_MESSAGE: ClassVar[str] = "AI provider authentication failed."


class ProviderRateLimitError(ProviderError):
    """Raised when an AI provider rejects a request due to rate limits."""

    DEFAULT_MESSAGE: ClassVar[str] = "The AI provider rate limit was exceeded."


class InvalidProviderResponseError(ProviderError):
    """Raised when an AI provider returns an invalid response."""

    DEFAULT_MESSAGE: ClassVar[str] = "The AI provider returned an invalid response."
