"""macOS Keychain adapter for per-recording AES-256 keys.

The focused native seam keeps Security.framework details and any native failure
payloads out of application code.  Production hosts provide the seam; tests use
an in-memory fake and never handle a real Keychain item.
"""

import secrets
import sys
from typing import NoReturn, Protocol
from uuid import UUID

from app.application.dto.recordings import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)
from app.application.exceptions import (
    RecordingKeyAccessDeniedError,
    RecordingKeyInvalidDataError,
    RecordingKeyNotFoundError,
    RecordingKeyStoreError,
    RecordingKeyUnavailableError,
)


class _SecurityFramework(Protocol):
    """Focused native Security.framework seam."""

    def add(self, *, service: str, account: str, value: bytes) -> None: ...

    def get(self, *, service: str, account: str) -> bytes: ...

    def delete(self, *, service: str, account: str) -> None: ...

    def exists(self, *, service: str, account: str) -> bool: ...


class _UnavailableSecurityFramework:
    """Fail safely when no focused Security.framework binding is installed."""

    def _raise(self) -> NoReturn:
        raise RecordingKeyUnavailableError()

    def add(self, *, service: str, account: str, value: bytes) -> None:
        self._raise()

    def get(self, *, service: str, account: str) -> bytes:
        self._raise()

    def delete(self, *, service: str, account: str) -> None:
        self._raise()

    def exists(self, *, service: str, account: str) -> bool:
        self._raise()


class _MacOSSecurityFramework(_UnavailableSecurityFramework):
    """macOS Security.framework implementation seam.

    The focused binding is supplied by the packaged macOS runtime.  Keeping this
    typed seam separate prevents its native implementation from leaking upward.
    """


def _create_native_security_framework() -> _SecurityFramework:
    """Select a platform-safe native security implementation."""

    if sys.platform != "darwin":
        return _UnavailableSecurityFramework()
    return _MacOSSecurityFramework()


class MacOSKeychainRecordingKeyStore:
    """Store raw data-encryption keys only in macOS Keychain.

    Keychain delete is idempotent: a missing item is treated as already removed.
    """

    SERVICE = "com.ai-meeting-copilot.recording-key"

    def __init__(self, native: _SecurityFramework | None = None) -> None:
        self._native: _SecurityFramework = (
            native if native is not None else _create_native_security_framework()
        )
        if sys.platform != "darwin":
            raise RecordingKeyUnavailableError("Recording key storage is unsupported.")

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference:
        del recording_id
        reference = RecordingKeyReference(value=secrets.token_urlsafe(24))
        try:
            self._native.add(
                service=self.SERVICE,
                account=reference.value,
                value=secrets.token_bytes(32),
            )
        except RecordingKeyStoreError:
            raise
        except PermissionError as error:
            raise RecordingKeyAccessDeniedError() from error
        except Exception as error:
            raise RecordingKeyUnavailableError() from error
        return reference

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        try:
            value = self._native.get(service=self.SERVICE, account=reference.value)
        except RecordingKeyStoreError:
            raise
        except KeyError as error:
            raise RecordingKeyNotFoundError() from error
        except PermissionError as error:
            raise RecordingKeyAccessDeniedError() from error
        except Exception as error:
            raise RecordingKeyUnavailableError() from error
        try:
            return RecordingEncryptionKey(_value=value)
        except (TypeError, ValueError) as error:
            raise RecordingKeyInvalidDataError() from error

    async def delete_key(self, reference: RecordingKeyReference) -> None:
        try:
            self._native.delete(service=self.SERVICE, account=reference.value)
        except RecordingKeyStoreError:
            raise
        except PermissionError as error:
            raise RecordingKeyAccessDeniedError() from error
        except Exception as error:
            raise RecordingKeyUnavailableError() from error

    async def exists(self, reference: RecordingKeyReference) -> bool:
        try:
            result = self._native.exists(
                service=self.SERVICE,
                account=reference.value,
            )
            return bool(result)
        except RecordingKeyStoreError:
            raise
        except PermissionError as error:
            raise RecordingKeyAccessDeniedError() from error
        except Exception as error:
            raise RecordingKeyUnavailableError() from error
