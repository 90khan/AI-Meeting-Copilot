"""Strict, privacy-safe WebSocket messages for transient Assist Mode updates."""

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import WebSocket

from app.api.live_transcription.protocol import PROTOCOL_VERSION
from app.application.dto.ai import GermanLevel, ReplySuggestion
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
)
from app.application.exceptions import ApplicationValidationError
from app.application.services import AssistUpdateSink


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistSegmentUpdateMessage:
    """One translation or simplification update for a persisted transcript."""

    version: int
    transcript_id: UUID
    capability: AssistCapability
    state: AssistState
    translated_text: str | None = None
    simplified_text: str | None = None
    target_level: GermanLevel | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        """Require a non-contradictory, capability-specific result shape."""

        _validate_version(self.version)
        if self.capability not in {
            AssistCapability.TRANSLATION,
            AssistCapability.SIMPLIFICATION,
        }:
            raise ApplicationValidationError("Assist capability is invalid.")
        if not isinstance(self.transcript_id, UUID):
            raise ApplicationValidationError("Transcript ID must be a UUID.")
        if not isinstance(self.state, AssistState):
            raise ApplicationValidationError("Assist state is invalid.")
        _validate_message(self.message)
        if self.state is AssistState.READY:
            if self.capability is AssistCapability.TRANSLATION:
                if not _is_non_blank(self.translated_text) or any(
                    value is not None
                    for value in (self.simplified_text, self.target_level, self.message)
                ):
                    raise ApplicationValidationError(
                        "Assist result fields are invalid."
                    )
            elif (
                not _is_non_blank(self.simplified_text)
                or not isinstance(self.target_level, GermanLevel)
                or any(
                    value is not None for value in (self.translated_text, self.message)
                )
            ):
                raise ApplicationValidationError("Assist result fields are invalid.")
        elif any(
            value is not None
            for value in (
                self.translated_text,
                self.simplified_text,
                self.target_level,
            )
        ):
            raise ApplicationValidationError("Assist result fields are invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistReplySuggestionsMessage:
    """One transient reply-coaching update anchored to a transcript segment."""

    version: int
    anchor_transcript_id: UUID
    state: AssistState
    suggestions: tuple[ReplySuggestion, ...] = ()
    message: str | None = None

    def __post_init__(self) -> None:
        """Keep reply results bounded and omit them outside the ready state."""

        _validate_version(self.version)
        if not isinstance(self.anchor_transcript_id, UUID):
            raise ApplicationValidationError("Transcript ID must be a UUID.")
        if not isinstance(self.state, AssistState):
            raise ApplicationValidationError("Assist state is invalid.")
        _validate_message(self.message)
        if self.state is AssistState.READY:
            if (
                not 1 <= len(self.suggestions) <= 2
                or not all(
                    isinstance(suggestion, ReplySuggestion)
                    for suggestion in self.suggestions
                )
                or self.message is not None
            ):
                raise ApplicationValidationError("Reply suggestions are invalid.")
        elif self.suggestions:
            raise ApplicationValidationError("Reply suggestions are invalid.")


type AssistMessage = AssistSegmentUpdateMessage | AssistReplySuggestionsMessage


def serialize_assist_message(message: AssistMessage) -> str:
    """Serialize one strict Assist Mode message without internal metadata."""

    if isinstance(message, AssistSegmentUpdateMessage):
        payload: dict[str, object] = {
            "type": "assist_segment_update",
            "version": message.version,
            "transcript_id": str(message.transcript_id),
            "capability": message.capability.value,
            "state": message.state.value,
        }
        if message.state is AssistState.READY:
            if message.capability is AssistCapability.TRANSLATION:
                payload["translated_text"] = message.translated_text
            else:
                payload["simplified_text"] = message.simplified_text
                target_level = message.target_level
                assert target_level is not None
                payload["target_level"] = target_level.value
        elif message.message is not None:
            payload["message"] = message.message
    elif isinstance(message, AssistReplySuggestionsMessage):
        payload = {
            "type": "assist_reply_suggestions",
            "version": message.version,
            "anchor_transcript_id": str(message.anchor_transcript_id),
            "state": message.state.value,
        }
        if message.state is AssistState.READY:
            payload["suggestions"] = [
                {"text": suggestion.text, "tone": suggestion.tone.value}
                for suggestion in message.suggestions
            ]
        elif message.message is not None:
            payload["message"] = message.message
    else:
        raise ApplicationValidationError("Assist message type is invalid.")
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class WebSocketAssistUpdateSink(AssistUpdateSink):
    """Publish application updates through one connection's serialized text writer."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        send_lock: asyncio.Lock,
        simplification_level: GermanLevel | None,
    ) -> None:
        """Bind the sink to a session-specific websocket send lock."""

        self._websocket = websocket
        self._send_lock = send_lock
        self._simplification_level = simplification_level

    async def publish(self, update: AssistUpdate) -> None:
        """Map a capability update and write exactly one serialized text frame."""

        message = self._to_message(update)
        async with self._send_lock:
            await self._websocket.send_text(serialize_assist_message(message))

    def _to_message(self, update: AssistUpdate) -> AssistMessage:
        """Convert an application update without introducing transport details."""

        if update.capability is AssistCapability.REPLY_COACHING:
            return AssistReplySuggestionsMessage(
                version=PROTOCOL_VERSION,
                anchor_transcript_id=update.transcript_id,
                state=update.state,
                suggestions=update.reply_suggestions or (),
            )
        return AssistSegmentUpdateMessage(
            version=PROTOCOL_VERSION,
            transcript_id=update.transcript_id,
            capability=update.capability,
            state=update.state,
            translated_text=update.translated_text,
            simplified_text=update.simplified_text,
            target_level=(
                self._simplification_level
                if update.capability is AssistCapability.SIMPLIFICATION
                and update.state is AssistState.READY
                else None
            ),
        )


def _validate_version(version: int) -> None:
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != PROTOCOL_VERSION
    ):
        raise ApplicationValidationError("Unsupported protocol version.")


def _validate_message(message: str | None) -> None:
    if message is not None and not _is_non_blank(message):
        raise ApplicationValidationError("Assist message must not be blank.")


def _is_non_blank(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())
