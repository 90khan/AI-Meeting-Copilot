"""Tests for strict, transport-neutral live-transcription control messages."""

import json
from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from app.api.live_transcription.protocol import (
    PROTOCOL_VERSION,
    EndSessionMessage,
    HelloAckMessage,
    HelloMessage,
    ProtocolErrorMessage,
    SessionStartedMessage,
    SessionStoppedMessage,
    StartSessionMessage,
    parse_protocol_message,
    serialize_protocol_message,
)
from app.application.dto.ai import LanguageCode
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_CLIENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_CONNECTION_ID = UUID("22222222-2222-2222-2222-222222222222")
_REQUEST_ID = UUID("33333333-3333-3333-3333-333333333333")
_SESSION_ID = UUID("44444444-4444-4444-4444-444444444444")
_MEETING_ID = MeetingId(UUID("55555555-5555-5555-5555-555555555555"))


@pytest.mark.parametrize(
    ("message", "message_type"),
    [
        (HelloMessage(version=1, token="local-token", client_id=_CLIENT_ID), "hello"),
        (
            HelloAckMessage(
                version=1,
                connection_id=_CONNECTION_ID,
                max_binary_payload_bytes=524_288,
                max_in_flight_chunks=1,
            ),
            "hello_ack",
        ),
        (
            StartSessionMessage(
                version=1,
                request_id=_REQUEST_ID,
                meeting_id=_MEETING_ID,
                language_hint=LanguageCode(value="de-DE"),
                source=AudioSource.MIXED,
            ),
            "start_session",
        ),
        (
            SessionStartedMessage(
                version=1,
                request_id=_REQUEST_ID,
                session_id=_SESSION_ID,
                expected_sequence=2,
            ),
            "session_started",
        ),
        (
            EndSessionMessage(
                version=1,
                request_id=_REQUEST_ID,
                session_id=_SESSION_ID,
                last_sequence=3,
            ),
            "end_session",
        ),
        (
            SessionStoppedMessage(
                version=1,
                request_id=_REQUEST_ID,
                session_id=_SESSION_ID,
            ),
            "session_stopped",
        ),
        (
            ProtocolErrorMessage(
                version=1,
                code="sequence_gap",
                message="The next chunk sequence is required.",
                fatal=False,
                session_id=_SESSION_ID,
                request_id=_REQUEST_ID,
                expected_sequence=4,
            ),
            "error",
        ),
    ],
)
def test_protocol_messages_round_trip(message: object, message_type: str) -> None:
    """Every supported control DTO has a stable discriminator and round trip."""

    serialized = serialize_protocol_message(message)  # type: ignore[arg-type]

    assert json.loads(serialized)["type"] == message_type
    assert parse_protocol_message(serialized) == message


def test_message_dtos_are_immutable() -> None:
    """Control messages retain their value-object semantics."""

    message = HelloMessage(version=1, token="local-token", client_id=_CLIENT_ID)

    with pytest.raises(FrozenInstanceError):
        message.token = "replacement"  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '{"type":"unsupported","version":1}',
        '{"type":"hello","version":2,"token":"token","client_id":"11111111-1111-1111-1111-111111111111"}',
        '{"type":"hello","version":1,"token":"token","client_id":"11111111-1111-1111-1111-111111111111","extra":true}',
    ],
)
def test_invalid_protocol_type_version_or_fields_are_rejected(payload: str) -> None:
    """Unknown message types, versions, and fields cannot enter the protocol."""

    with pytest.raises(ApplicationValidationError):
        parse_protocol_message(payload)


def test_missing_type_discriminator_is_rejected() -> None:
    """JSON controls must identify their message schema explicitly."""

    with pytest.raises(ApplicationValidationError, match="type"):
        parse_protocol_message('{"version":1}')


@pytest.mark.parametrize("token", ["", "   "])
def test_blank_tokens_are_rejected(token: str) -> None:
    """Hello tokens cannot be blank."""

    with pytest.raises(ApplicationValidationError, match="Token"):
        HelloMessage(version=PROTOCOL_VERSION, token=token, client_id=_CLIENT_ID)


@pytest.mark.parametrize("sequence", [-1, True])
def test_invalid_sequences_are_rejected(sequence: int) -> None:
    """Sequence counters are strict non-negative integers."""

    with pytest.raises(ApplicationValidationError, match="sequence"):
        EndSessionMessage(
            version=PROTOCOL_VERSION,
            request_id=_REQUEST_ID,
            session_id=_SESSION_ID,
            last_sequence=sequence,
        )


@pytest.mark.parametrize("limit", [0, -1, True])
def test_non_positive_acknowledged_limits_are_rejected(limit: int) -> None:
    """Negotiated frame and in-flight limits must remain positive."""

    with pytest.raises(ApplicationValidationError):
        HelloAckMessage(
            version=1,
            connection_id=_CONNECTION_ID,
            max_binary_payload_bytes=limit,
            max_in_flight_chunks=1,
        )
