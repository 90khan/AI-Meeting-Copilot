"""Authentication boundary for the local desktop sidecar."""

import secrets

from app.application.exceptions import ProviderAuthenticationError


class SidecarTokenValidator:
    """Validate one configured sidecar token without exposing its value."""

    __slots__ = ("_expected_token",)

    def __init__(self, expected_token: str) -> None:
        """Create a validator for one non-blank expected token."""

        if not isinstance(expected_token, str) or not expected_token.strip():
            raise ValueError("Sidecar authentication token must not be blank.")
        self._expected_token = expected_token

    def __repr__(self) -> str:
        """Return a representation that intentionally excludes the secret."""

        return "SidecarTokenValidator()"

    def validate(self, supplied_token: str) -> None:
        """Accept only an exact token match using a constant-time comparison."""

        if not isinstance(supplied_token, str) or not supplied_token.strip():
            raise ProviderAuthenticationError("Sidecar authentication failed.")
        if not secrets.compare_digest(self._expected_token, supplied_token):
            raise ProviderAuthenticationError("Sidecar authentication failed.")
