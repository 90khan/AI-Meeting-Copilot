"""Tests for the Keychain adapter through a fake native Security seam."""

import asyncio
from uuid import uuid4

import pytest
from app.application.dto.recordings import RecordingKeyReference
from app.application.exceptions import (
    RecordingKeyInvalidDataError,
    RecordingKeyNotFoundError,
    RecordingKeyUnavailableError,
)
from app.infrastructure.recordings import keychain_key_store
from app.infrastructure.recordings.keychain_key_store import (
    MacOSKeychainRecordingKeyStore,
    _create_native_security_framework,
    _MacOSSecurityFramework,
    _UnavailableSecurityFramework,
)


class FakeNative:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def add(self, *, service: str, account: str, value: bytes) -> None:
        if account in self.items:
            raise RuntimeError()
        self.items[account] = value

    def get(self, *, service: str, account: str) -> bytes:
        if account not in self.items:
            raise KeyError(account)
        return self.items[account]

    def delete(self, *, service: str, account: str) -> None:
        self.items.pop(account, None)

    def exists(self, *, service: str, account: str) -> bool:
        return account in self.items


def test_keychain_adapter_stores_gets_deletes_and_validates_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keychain_key_store, "_current_platform", lambda: "Darwin")
    native = FakeNative()
    store = MacOSKeychainRecordingKeyStore(native)

    reference = asyncio.run(store.create_key(uuid4()))
    key = asyncio.run(store.get_key(reference))
    assert len(key._material()) == 32
    assert asyncio.run(store.exists(reference)) is True
    asyncio.run(store.delete_key(reference))
    assert asyncio.run(store.exists(reference)) is False
    with pytest.raises(RecordingKeyNotFoundError):
        asyncio.run(store.get_key(reference))


def test_keychain_adapter_maps_invalid_data_and_platform_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keychain_key_store, "_current_platform", lambda: "Darwin")
    native = FakeNative()
    reference = RecordingKeyReference(value="a" * 24)
    native.items[reference.value] = b"invalid"
    store = MacOSKeychainRecordingKeyStore(native)
    with pytest.raises(RecordingKeyInvalidDataError):
        asyncio.run(store.get_key(reference))

    monkeypatch.setattr(keychain_key_store, "_current_platform", lambda: "Linux")
    with pytest.raises(RecordingKeyUnavailableError):
        MacOSKeychainRecordingKeyStore(native)


def test_native_factory_selects_by_injected_platform_name() -> None:
    assert isinstance(
        _create_native_security_framework(platform_name="Darwin"),
        _MacOSSecurityFramework,
    )
    assert isinstance(
        _create_native_security_framework(platform_name="Linux"),
        _UnavailableSecurityFramework,
    )


def test_injected_native_takes_precedence_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keychain_key_store, "_current_platform", lambda: "Darwin")
    native = FakeNative()
    store = MacOSKeychainRecordingKeyStore(native)

    assert store._native is native
