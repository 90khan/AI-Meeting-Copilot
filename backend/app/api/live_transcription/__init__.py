"""Transport-neutral protocol definitions for live transcription."""

from app.api.live_transcription.assist_messages import (
    AssistReplySuggestionsMessage,
    AssistSegmentUpdateMessage,
    WebSocketAssistUpdateSink,
    serialize_assist_message,
)
from app.api.live_transcription.binary_frames import (
    AudioChunkFrameMetadata,
    build_audio_chunk_frame,
    parse_audio_chunk_frame,
)
from app.api.live_transcription.protocol import (
    AUDIO_FRAME_MAGIC,
    AUDIO_MESSAGE_KIND,
    DEFAULT_MAX_BINARY_PAYLOAD_BYTES,
    DEFAULT_MAX_IN_FLIGHT_CHUNKS,
    PROTOCOL_VERSION,
    AssistModeSessionConfiguration,
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

__all__ = [
    "AUDIO_FRAME_MAGIC",
    "AUDIO_MESSAGE_KIND",
    "DEFAULT_MAX_BINARY_PAYLOAD_BYTES",
    "DEFAULT_MAX_IN_FLIGHT_CHUNKS",
    "PROTOCOL_VERSION",
    "AssistModeSessionConfiguration",
    "AssistReplySuggestionsMessage",
    "AssistSegmentUpdateMessage",
    "AudioChunkFrameMetadata",
    "EndSessionMessage",
    "HelloAckMessage",
    "HelloMessage",
    "ProtocolErrorMessage",
    "SessionStartedMessage",
    "SessionStoppedMessage",
    "StartSessionMessage",
    "WebSocketAssistUpdateSink",
    "build_audio_chunk_frame",
    "parse_audio_chunk_frame",
    "parse_protocol_message",
    "serialize_assist_message",
    "serialize_protocol_message",
]
