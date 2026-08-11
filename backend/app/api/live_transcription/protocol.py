"""Transport-neutral control messages for live transcription."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.application.dto.ai import GermanLevel, LanguageCode
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

PROTOCOL_VERSION = 1
AUDIO_FRAME_MAGIC = b"AMCP"
AUDIO_MESSAGE_KIND = 1
DEFAULT_MAX_BINARY_PAYLOAD_BYTES = 524_288
DEFAULT_MAX_IN_FLIGHT_CHUNKS = 1


class SessionStartFailureStage(StrEnum):
    """Allowlisted diagnostics for unexpected session-start failures only."""

    MEETING_VALIDATION = "session_meeting_validation"
    FACTORY = "session_factory"
    ASSIST_INITIALIZATION = "session_assist_initialization"
    STARTED_SEND = "session_started_send"


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistModeSessionConfiguration:
    """Client-selected, provider-agnostic Assist Mode settings for one session."""

    enabled: bool
    translation_enabled: bool
    simplification_enabled: bool
    simplification_level: GermanLevel | None
    reply_coaching_enabled: bool

    def __post_init__(self) -> None:
        """Validate the minimal V1 assist configuration without provider names."""

        if not all(
            isinstance(value, bool)
            for value in (
                self.enabled,
                self.translation_enabled,
                self.simplification_enabled,
                self.reply_coaching_enabled,
            )
        ):
            raise ApplicationValidationError("Assist Mode flags must be booleans.")
        if self.simplification_enabled:
            if self.simplification_level not in {GermanLevel.B1, GermanLevel.B2}:
                raise ApplicationValidationError(
                    "Assist Mode simplification level must be B1 or B2."
                )
        elif self.simplification_level is not None:
            raise ApplicationValidationError(
                "Assist Mode simplification level is not applicable."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class HelloMessage:
    """Client authentication and protocol-negotiation message."""

    version: int
    token: str
    client_id: UUID

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)
        _validate_non_blank(self.token, "Token")


@dataclass(frozen=True, slots=True, kw_only=True)
class HelloAckMessage:
    """Server acknowledgement of a successful client hello."""

    version: int
    connection_id: UUID
    max_binary_payload_bytes: int
    max_in_flight_chunks: int

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)
        _validate_positive_integer(
            self.max_binary_payload_bytes, "Maximum binary payload size"
        )
        _validate_positive_integer(
            self.max_in_flight_chunks, "Maximum in-flight chunk count"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StartSessionMessage:
    """Client request to begin a Meeting's live-transcription session."""

    version: int
    request_id: UUID
    meeting_id: MeetingId
    language_hint: LanguageCode | None
    source: AudioSource
    assist_mode: AssistModeSessionConfiguration | None = None

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)
        if not isinstance(self.meeting_id, MeetingId):
            raise ApplicationValidationError("Meeting ID must be a MeetingId.")
        if self.language_hint is not None and not isinstance(
            self.language_hint, LanguageCode
        ):
            raise ApplicationValidationError("Language hint must be a LanguageCode.")
        if not isinstance(self.source, AudioSource):
            raise ApplicationValidationError("Audio source is invalid.")
        if self.assist_mode is not None and not isinstance(
            self.assist_mode, AssistModeSessionConfiguration
        ):
            raise ApplicationValidationError("Assist Mode configuration is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionStartedMessage:
    """Server confirmation that a live-transcription session is active."""

    version: int
    request_id: UUID
    session_id: UUID
    expected_sequence: int = 0

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)
        _validate_non_negative_integer(self.expected_sequence, "Expected sequence")


@dataclass(frozen=True, slots=True, kw_only=True)
class EndSessionMessage:
    """Client request to stop its active live-transcription session."""

    version: int
    request_id: UUID
    session_id: UUID
    last_sequence: int

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)
        _validate_non_negative_integer(self.last_sequence, "Last sequence")


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionStoppedMessage:
    """Server confirmation that a session has stopped."""

    version: int
    request_id: UUID
    session_id: UUID

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolErrorMessage:
    """A privacy-safe protocol error response without payload details."""

    version: int
    code: str
    message: str
    fatal: bool
    session_id: UUID | None = None
    request_id: UUID | None = None
    expected_sequence: int | None = None
    session_start_stage: SessionStartFailureStage | None = None

    def __post_init__(self) -> None:
        _validate_protocol_version(self.version)
        _validate_non_blank(self.code, "Error code")
        _validate_non_blank(self.message, "Error message")
        if not isinstance(self.fatal, bool):
            raise ApplicationValidationError("Error fatal flag must be a boolean.")
        if self.expected_sequence is not None:
            _validate_non_negative_integer(self.expected_sequence, "Expected sequence")
        if self.session_start_stage is not None and not isinstance(
            self.session_start_stage, SessionStartFailureStage
        ):
            raise ApplicationValidationError("Session start failure stage is invalid.")


type ProtocolMessage = (
    HelloMessage
    | HelloAckMessage
    | StartSessionMessage
    | SessionStartedMessage
    | EndSessionMessage
    | SessionStoppedMessage
    | ProtocolErrorMessage
)

_MESSAGE_TYPES: dict[type[ProtocolMessage], str] = {
    HelloMessage: "hello",
    HelloAckMessage: "hello_ack",
    StartSessionMessage: "start_session",
    SessionStartedMessage: "session_started",
    EndSessionMessage: "end_session",
    SessionStoppedMessage: "session_stopped",
    ProtocolErrorMessage: "error",
}


def serialize_protocol_message(message: ProtocolMessage) -> str:
    """Serialize one control message using its required type discriminator."""

    message_type = _MESSAGE_TYPES.get(type(message))
    if message_type is None:
        raise ApplicationValidationError("Unsupported protocol message type.")

    payload = _message_to_payload(message, message_type)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def parse_protocol_message(payload: str | bytes) -> ProtocolMessage:
    """Parse a strict JSON control message without accepting unknown fields."""

    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ApplicationValidationError(
                "Protocol message must be valid UTF-8."
            ) from error
    if not isinstance(payload, str):
        raise ApplicationValidationError("Protocol message must be JSON text.")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ApplicationValidationError(
            "Protocol message must contain valid JSON."
        ) from error
    if not isinstance(data, dict):
        raise ApplicationValidationError("Protocol message must be a JSON object.")

    message_type = data.get("type")
    if not isinstance(message_type, str):
        raise ApplicationValidationError("Protocol message type is required.")

    parser = _MESSAGE_PARSERS.get(message_type)
    if parser is None:
        raise ApplicationValidationError("Unsupported protocol message type.")
    return parser(data)


def _message_to_payload(
    message: ProtocolMessage, message_type: str
) -> dict[str, object]:
    """Convert a validated message to primitive JSON data."""

    payload: dict[str, object] = {"type": message_type, "version": message.version}
    match message:
        case HelloMessage(token=token, client_id=client_id):
            payload.update(token=token, client_id=str(client_id))
        case HelloAckMessage(
            connection_id=connection_id,
            max_binary_payload_bytes=max_binary_payload_bytes,
            max_in_flight_chunks=max_in_flight_chunks,
        ):
            payload.update(
                connection_id=str(connection_id),
                max_binary_payload_bytes=max_binary_payload_bytes,
                max_in_flight_chunks=max_in_flight_chunks,
            )
        case StartSessionMessage(
            request_id=request_id,
            meeting_id=meeting_id,
            language_hint=language_hint,
            source=source,
            assist_mode=assist_mode,
        ):
            payload.update(
                request_id=str(request_id),
                meeting_id=str(meeting_id),
                language_hint=(
                    str(language_hint) if language_hint is not None else None
                ),
                source=source.value,
            )
            if assist_mode is not None:
                payload["assist_mode"] = _assist_mode_to_payload(assist_mode)
        case SessionStartedMessage(
            request_id=request_id,
            session_id=session_id,
            expected_sequence=expected_sequence,
        ):
            payload.update(
                request_id=str(request_id),
                session_id=str(session_id),
                expected_sequence=expected_sequence,
            )
        case EndSessionMessage(
            request_id=request_id, session_id=session_id, last_sequence=last_sequence
        ):
            payload.update(
                request_id=str(request_id),
                session_id=str(session_id),
                last_sequence=last_sequence,
            )
        case SessionStoppedMessage(request_id=request_id, session_id=session_id):
            payload.update(request_id=str(request_id), session_id=str(session_id))
        case ProtocolErrorMessage(
            code=code,
            message=error_message,
            fatal=fatal,
            session_id=session_id,
            request_id=request_id,
            expected_sequence=expected_sequence,
            session_start_stage=session_start_stage,
        ):
            payload.update(
                code=code,
                message=error_message,
                fatal=fatal,
                session_id=(str(session_id) if session_id is not None else None),
                request_id=(str(request_id) if request_id is not None else None),
                expected_sequence=expected_sequence,
                session_start_stage=(
                    session_start_stage.value
                    if session_start_stage is not None
                    else None
                ),
            )
    return payload


def _parse_hello(data: dict[str, object]) -> HelloMessage:
    _require_fields(data, {"type", "version", "token", "client_id"})
    return HelloMessage(
        version=_require_integer(data, "version"),
        token=_require_string(data, "token"),
        client_id=_parse_uuid(data, "client_id"),
    )


def _parse_hello_ack(data: dict[str, object]) -> HelloAckMessage:
    _require_fields(
        data,
        {
            "type",
            "version",
            "connection_id",
            "max_binary_payload_bytes",
            "max_in_flight_chunks",
        },
    )
    return HelloAckMessage(
        version=_require_integer(data, "version"),
        connection_id=_parse_uuid(data, "connection_id"),
        max_binary_payload_bytes=_require_integer(data, "max_binary_payload_bytes"),
        max_in_flight_chunks=_require_integer(data, "max_in_flight_chunks"),
    )


def _parse_start_session(data: dict[str, object]) -> StartSessionMessage:
    required_fields = {
        "type",
        "version",
        "request_id",
        "meeting_id",
        "language_hint",
        "source",
    }
    provided_fields = set(data)
    if provided_fields != required_fields and provided_fields != (
        required_fields | {"assist_mode"}
    ):
        raise ApplicationValidationError("Protocol message fields are invalid.")
    language_hint = _optional_language_code(data, "language_hint")
    assist_mode = (
        _assist_mode_configuration(data["assist_mode"])
        if "assist_mode" in data
        else None
    )
    try:
        source = AudioSource(_require_string(data, "source"))
    except ValueError as error:
        raise ApplicationValidationError("Audio source is invalid.") from error
    return StartSessionMessage(
        version=_require_integer(data, "version"),
        request_id=_parse_uuid(data, "request_id"),
        meeting_id=MeetingId(_parse_uuid(data, "meeting_id")),
        language_hint=language_hint,
        source=source,
        assist_mode=assist_mode,
    )


def _parse_session_started(data: dict[str, object]) -> SessionStartedMessage:
    _require_fields(
        data,
        {"type", "version", "request_id", "session_id", "expected_sequence"},
    )
    return SessionStartedMessage(
        version=_require_integer(data, "version"),
        request_id=_parse_uuid(data, "request_id"),
        session_id=_parse_uuid(data, "session_id"),
        expected_sequence=_require_integer(data, "expected_sequence"),
    )


def _parse_end_session(data: dict[str, object]) -> EndSessionMessage:
    _require_fields(
        data,
        {"type", "version", "request_id", "session_id", "last_sequence"},
    )
    return EndSessionMessage(
        version=_require_integer(data, "version"),
        request_id=_parse_uuid(data, "request_id"),
        session_id=_parse_uuid(data, "session_id"),
        last_sequence=_require_integer(data, "last_sequence"),
    )


def _parse_session_stopped(data: dict[str, object]) -> SessionStoppedMessage:
    _require_fields(data, {"type", "version", "request_id", "session_id"})
    return SessionStoppedMessage(
        version=_require_integer(data, "version"),
        request_id=_parse_uuid(data, "request_id"),
        session_id=_parse_uuid(data, "session_id"),
    )


def _parse_error(data: dict[str, object]) -> ProtocolErrorMessage:
    _require_fields(
        data,
        {
            "type",
            "version",
            "code",
            "message",
            "fatal",
            "session_id",
            "request_id",
            "expected_sequence",
            "session_start_stage",
        },
    )
    fatal = data["fatal"]
    if not isinstance(fatal, bool):
        raise ApplicationValidationError("Error fatal flag must be a boolean.")
    return ProtocolErrorMessage(
        version=_require_integer(data, "version"),
        code=_require_string(data, "code"),
        message=_require_string(data, "message"),
        fatal=fatal,
        session_id=_parse_optional_uuid(data, "session_id"),
        request_id=_parse_optional_uuid(data, "request_id"),
        expected_sequence=_optional_integer(data, "expected_sequence"),
        session_start_stage=_optional_session_start_failure_stage(
            data, "session_start_stage"
        ),
    )


type _MessageParser = Callable[[dict[str, object]], ProtocolMessage]
_MESSAGE_PARSERS: dict[str, _MessageParser] = {
    "hello": _parse_hello,
    "hello_ack": _parse_hello_ack,
    "start_session": _parse_start_session,
    "session_started": _parse_session_started,
    "end_session": _parse_end_session,
    "session_stopped": _parse_session_stopped,
    "error": _parse_error,
}


def _require_fields(data: dict[str, object], expected: set[str]) -> None:
    """Require an exact, versioned message schema."""

    if set(data) != expected:
        raise ApplicationValidationError("Protocol message fields are invalid.")


def _require_string(data: dict[str, object], field_name: str) -> str:
    value = data[field_name]
    if not isinstance(value, str):
        raise ApplicationValidationError("Protocol message field is invalid.")
    return value


def _require_integer(data: dict[str, object], field_name: str) -> int:
    value = data[field_name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApplicationValidationError("Protocol message field is invalid.")
    return value


def _optional_integer(data: dict[str, object], field_name: str) -> int | None:
    value = data[field_name]
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApplicationValidationError("Protocol message field is invalid.")
    return value


def _parse_uuid(data: dict[str, object], field_name: str) -> UUID:
    try:
        return UUID(_require_string(data, field_name))
    except ValueError as error:
        raise ApplicationValidationError("Protocol message UUID is invalid.") from error


def _parse_optional_uuid(data: dict[str, object], field_name: str) -> UUID | None:
    if data[field_name] is None:
        return None
    return _parse_uuid(data, field_name)


def _optional_session_start_failure_stage(
    data: dict[str, object], field_name: str
) -> SessionStartFailureStage | None:
    """Parse the optional, closed-set diagnostic without accepting arbitrary text."""

    if data[field_name] is None:
        return None
    try:
        return SessionStartFailureStage(_require_string(data, field_name))
    except ValueError as error:
        raise ApplicationValidationError(
            "Session start failure stage is invalid."
        ) from error


def _optional_language_code(
    data: dict[str, object], field_name: str
) -> LanguageCode | None:
    if data[field_name] is None:
        return None
    return LanguageCode(value=_require_string(data, field_name))


def _assist_mode_to_payload(
    configuration: AssistModeSessionConfiguration,
) -> dict[str, object]:
    """Convert a validated Assist Mode configuration to strict JSON primitives."""

    return {
        "enabled": configuration.enabled,
        "translation_enabled": configuration.translation_enabled,
        "simplification_enabled": configuration.simplification_enabled,
        "simplification_level": (
            configuration.simplification_level.value
            if configuration.simplification_level is not None
            else None
        ),
        "reply_coaching_enabled": configuration.reply_coaching_enabled,
    }


def _assist_mode_configuration(value: object) -> AssistModeSessionConfiguration:
    """Parse the optional exact-shape Assist Mode configuration object."""

    if not isinstance(value, dict):
        raise ApplicationValidationError("Assist Mode configuration is invalid.")
    expected = {
        "enabled",
        "translation_enabled",
        "simplification_enabled",
        "simplification_level",
        "reply_coaching_enabled",
    }
    if set(value) != expected:
        raise ApplicationValidationError(
            "Assist Mode configuration fields are invalid."
        )
    simplification_level_value = value["simplification_level"]
    try:
        simplification_level = (
            None
            if simplification_level_value is None
            else GermanLevel(_require_mapping_string(value, "simplification_level"))
        )
    except ValueError as error:
        raise ApplicationValidationError(
            "Assist Mode simplification level is invalid."
        ) from error
    return AssistModeSessionConfiguration(
        enabled=_require_mapping_bool(value, "enabled"),
        translation_enabled=_require_mapping_bool(value, "translation_enabled"),
        simplification_enabled=_require_mapping_bool(value, "simplification_enabled"),
        simplification_level=simplification_level,
        reply_coaching_enabled=_require_mapping_bool(value, "reply_coaching_enabled"),
    )


def _require_mapping_string(data: dict[str, object], field_name: str) -> str:
    value = data[field_name]
    if not isinstance(value, str):
        raise ApplicationValidationError("Assist Mode configuration is invalid.")
    return value


def _require_mapping_bool(data: dict[str, object], field_name: str) -> bool:
    value = data[field_name]
    if not isinstance(value, bool):
        raise ApplicationValidationError("Assist Mode configuration is invalid.")
    return value


def _validate_protocol_version(version: int) -> None:
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != PROTOCOL_VERSION
    ):
        raise ApplicationValidationError("Unsupported protocol version.")


def _validate_non_blank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ApplicationValidationError(f"{field_name} must not be blank.")


def _validate_non_negative_integer(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ApplicationValidationError(
            f"{field_name} must be a non-negative integer."
        )


def _validate_positive_integer(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ApplicationValidationError(f"{field_name} must be a positive integer.")
