"""Tests for privacy-safe Assist Mode WebSocket messages and their sink."""

import asyncio
import json
from uuid import UUID

import pytest
from app.api.live_transcription.assist_messages import (
    AssistReplySuggestionsMessage,
    AssistSegmentUpdateMessage,
    WebSocketAssistUpdateSink,
    serialize_assist_message,
)
from app.application.dto.ai import GermanLevel, ReplySuggestion, ReplyTone
from app.application.dto.assist_mode import AssistCapability, AssistState, AssistUpdate
from app.application.exceptions import ApplicationValidationError

_TRANSCRIPT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_segment_messages_enforce_capability_specific_result_shapes() -> None:
    """Translation and simplification results accept only their valid fields."""

    translation = AssistSegmentUpdateMessage(
        version=1,
        transcript_id=_TRANSCRIPT_ID,
        capability=AssistCapability.TRANSLATION,
        state=AssistState.READY,
        translated_text="Türkçe metin",
    )
    simplification = AssistSegmentUpdateMessage(
        version=1,
        transcript_id=_TRANSCRIPT_ID,
        capability=AssistCapability.SIMPLIFICATION,
        state=AssistState.READY,
        simplified_text="Einfacher Text",
        target_level=GermanLevel.B1,
    )

    assert json.loads(serialize_assist_message(translation)) == {
        "capability": "translation",
        "state": "ready",
        "transcript_id": str(_TRANSCRIPT_ID),
        "translated_text": "Türkçe metin",
        "type": "assist_segment_update",
        "version": 1,
    }
    assert json.loads(serialize_assist_message(simplification))["target_level"] == "b1"

    with pytest.raises(ApplicationValidationError):
        AssistSegmentUpdateMessage(
            version=1,
            transcript_id=_TRANSCRIPT_ID,
            capability=AssistCapability.TRANSLATION,
            state=AssistState.READY,
            translated_text="text",
            target_level=GermanLevel.B1,
        )
    with pytest.raises(ApplicationValidationError):
        AssistSegmentUpdateMessage(
            version=1,
            transcript_id=_TRANSCRIPT_ID,
            capability=AssistCapability.SIMPLIFICATION,
            state=AssistState.PROCESSING,
            simplified_text="text",
        )


@pytest.mark.parametrize(
    ("state", "expected_fields"),
    [
        (
            AssistState.PROCESSING,
            {"type", "version", "transcript_id", "capability", "state"},
        ),
        (
            AssistState.FAILED,
            {"type", "version", "transcript_id", "capability", "state"},
        ),
        (
            AssistState.UNAVAILABLE,
            {"type", "version", "transcript_id", "capability", "state"},
        ),
    ],
)
def test_non_ready_translation_messages_serialize_the_common_contract(
    state: AssistState,
    expected_fields: set[str],
) -> None:
    """Non-ready translation states carry no result or provider details."""

    message = AssistSegmentUpdateMessage(
        version=1,
        transcript_id=_TRANSCRIPT_ID,
        capability=AssistCapability.TRANSLATION,
        state=state,
    )

    assert set(json.loads(serialize_assist_message(message))) == expected_fields


def test_reply_messages_are_bounded_and_omit_results_until_ready() -> None:
    """Reply payloads contain at most two user-facing suggestions when ready."""

    processing = AssistReplySuggestionsMessage(
        version=1,
        anchor_transcript_id=_TRANSCRIPT_ID,
        state=AssistState.PROCESSING,
    )
    ready = AssistReplySuggestionsMessage(
        version=1,
        anchor_transcript_id=_TRANSCRIPT_ID,
        state=AssistState.READY,
        suggestions=(ReplySuggestion(text="Ja, gerne.", tone=ReplyTone.PROFESSIONAL),),
    )

    assert "suggestions" not in json.loads(serialize_assist_message(processing))
    assert json.loads(serialize_assist_message(ready))["suggestions"] == [
        {"text": "Ja, gerne.", "tone": "professional"}
    ]
    with pytest.raises(ApplicationValidationError):
        AssistReplySuggestionsMessage(
            version=1,
            anchor_transcript_id=_TRANSCRIPT_ID,
            state=AssistState.FAILED,
            suggestions=(ReplySuggestion(text="No", tone=ReplyTone.NEUTRAL),),
        )


class _FakeWebSocket:
    """Minimal async text sender that exposes concurrent-write attempts."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.concurrent_sends = 0
        self.max_concurrent_sends = 0

    async def send_text(self, payload: str) -> None:
        """Record one frame after yielding to make lock behavior observable."""

        self.concurrent_sends += 1
        self.max_concurrent_sends = max(
            self.max_concurrent_sends,
            self.concurrent_sends,
        )
        await asyncio.sleep(0)
        self.messages.append(payload)
        self.concurrent_sends -= 1


def test_websocket_sink_serializes_concurrent_writes() -> None:
    """One connection lock prevents concurrent Assist text writes."""

    async def run() -> None:
        websocket = _FakeWebSocket()
        sink = WebSocketAssistUpdateSink(
            websocket=websocket,  # type: ignore[arg-type]
            send_lock=asyncio.Lock(),
            simplification_level=GermanLevel.B2,
        )
        update = AssistUpdate(
            transcript_id=_TRANSCRIPT_ID,
            capability=AssistCapability.SIMPLIFICATION,
            state=AssistState.READY,
            simplified_text="Einfacher Text",
        )

        await asyncio.gather(sink.publish(update), sink.publish(update))

        assert websocket.max_concurrent_sends == 1
        assert len(websocket.messages) == 2
        assert all(
            json.loads(message)["target_level"] == "b2"
            for message in websocket.messages
        )

    asyncio.run(run())
