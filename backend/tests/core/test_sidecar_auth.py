"""Tests for the local desktop-sidecar token boundary."""

import asyncio

import pytest
from app.application.exceptions import ProviderAuthenticationError
from app.core.config import Settings
from app.core.container import Container
from app.core.sidecar_auth import SidecarTokenValidator


def test_valid_sidecar_token_is_accepted() -> None:
    """An exact configured token passes validation."""

    SidecarTokenValidator(expected_token="expected-token").validate("expected-token")


def test_invalid_sidecar_token_is_rejected_without_leaking_secrets() -> None:
    """Authentication failures are generic and do not reveal either token."""

    with pytest.raises(ProviderAuthenticationError) as error:
        SidecarTokenValidator(expected_token="expected-token").validate("wrong-token")

    assert "expected-token" not in str(error.value)
    assert "wrong-token" not in str(error.value)


def test_compare_digest_seam_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validation delegates equality checks to the standard constant-time helper."""

    observed_arguments: list[tuple[str, str]] = []

    def compare_digest(expected: str, supplied: str) -> bool:
        observed_arguments.append((expected, supplied))
        return True

    monkeypatch.setattr("app.core.sidecar_auth.secrets.compare_digest", compare_digest)

    SidecarTokenValidator(expected_token="expected-token").validate("supplied-token")

    assert observed_arguments == [("expected-token", "supplied-token")]


@pytest.mark.parametrize("expected_token", ["", "   "])
def test_blank_expected_token_is_rejected(expected_token: str) -> None:
    """Validators cannot be created from blank secret configuration."""

    with pytest.raises(ValueError, match="must not be blank"):
        SidecarTokenValidator(expected_token=expected_token)


@pytest.mark.parametrize("supplied_token", ["", "   "])
def test_blank_supplied_token_is_rejected(supplied_token: str) -> None:
    """Clients must provide a non-blank handshake token."""

    with pytest.raises(ProviderAuthenticationError) as error:
        SidecarTokenValidator(expected_token="expected-token").validate(supplied_token)

    assert "expected-token" not in str(error.value)


def test_validator_representation_does_not_include_its_token() -> None:
    """Debug representations do not disclose token configuration."""

    assert "expected-token" not in repr(
        SidecarTokenValidator(expected_token="expected-token")
    )


def test_container_requires_explicit_sidecar_token_configuration() -> None:
    """Started containers provide clear guidance when no token was injected."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    asyncio.run(container.start())

    try:
        with pytest.raises(RuntimeError, match="SIDECAR_AUTH_TOKEN"):
            container.get_sidecar_token_validator()
    finally:
        asyncio.run(container.stop())


def test_container_caches_validator_for_one_active_lifecycle() -> None:
    """One started container resolves a single validator instance."""

    container = Container(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            sidecar_auth_token="expected-token",
        )
    )
    asyncio.run(container.start())

    try:
        first_validator = container.get_sidecar_token_validator()
        second_validator = container.get_sidecar_token_validator()

        assert first_validator is second_validator
    finally:
        asyncio.run(container.stop())


def test_container_stop_clears_validator_and_blocks_resolution() -> None:
    """Stopping removes the lifecycle-owned validator and closes access."""

    container = Container(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            sidecar_auth_token="expected-token",
        )
    )
    asyncio.run(container.start())
    validator = container.get_sidecar_token_validator()

    asyncio.run(container.stop())

    assert container._sidecar_token_validator is None
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_sidecar_token_validator()
    assert validator is not container._sidecar_token_validator
