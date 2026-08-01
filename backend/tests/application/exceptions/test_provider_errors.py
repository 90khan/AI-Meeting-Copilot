"""Tests for application-level AI provider errors."""

import pytest
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@pytest.mark.parametrize(
    ("error_type", "default_message"),
    [
        (ProviderError, "An AI provider error occurred."),
        (ProviderUnavailableError, "The AI provider is unavailable."),
        (ProviderTimeoutError, "The AI provider request timed out."),
        (ProviderAuthenticationError, "AI provider authentication failed."),
        (ProviderRateLimitError, "The AI provider rate limit was exceeded."),
        (
            InvalidProviderResponseError,
            "The AI provider returned an invalid response.",
        ),
    ],
)
def test_provider_errors_use_expected_default_messages(
    error_type: type[ProviderError],
    default_message: str,
) -> None:
    """Every provider error supplies a stable default message."""

    assert str(error_type()) == default_message


@pytest.mark.parametrize(
    "error_type",
    [
        ProviderUnavailableError,
        ProviderTimeoutError,
        ProviderAuthenticationError,
        ProviderRateLimitError,
        InvalidProviderResponseError,
    ],
)
def test_provider_errors_inherit_from_provider_error(
    error_type: type[ProviderError],
) -> None:
    """Specific provider failures remain catchable through ProviderError."""

    assert issubclass(error_type, ProviderError)


def test_provider_errors_preserve_explicit_messages() -> None:
    """Explicit provider error messages are not changed."""

    assert str(ProviderTimeoutError("The exact provider message.")) == (
        "The exact provider message."
    )
