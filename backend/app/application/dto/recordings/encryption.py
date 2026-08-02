"""Opaque recording encryption material used only at infrastructure boundaries."""

from dataclasses import dataclass, field
from re import fullmatch

from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingEncryptionKey:
    """A redacted, fixed-size AES-256 data-encryption key."""

    _value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._value, bytes) or len(self._value) != 32:
            raise ApplicationValidationError("Recording encryption key is invalid.")

    def __repr__(self) -> str:
        return "RecordingEncryptionKey(<redacted>)"

    def __str__(self) -> str:
        return "<redacted recording encryption key>"

    def _material(self) -> bytes:
        """Return key material only to trusted encryption infrastructure."""

        return self._value


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingKeyReference:
    """Opaque Keychain account identifier suitable for persisted metadata."""

    value: str

    def __post_init__(self) -> None:
        if fullmatch(r"[a-zA-Z0-9_-]{16,128}", self.value) is None:
            raise ApplicationValidationError("Recording key reference is invalid.")

    def __str__(self) -> str:
        return self.value
