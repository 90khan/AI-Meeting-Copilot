"""Tests for transient Assist Mode DTOs and independent enrichment use cases."""

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.dto.ai import (
    GermanLevel,
    GermanSimplificationResult,
    LanguageCode,
    ReplyCoachingResult,
    ReplySuggestion,
    ReplyTone,
    TranslationResult,
)
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
    ReplyContext,
    TranscriptSegment,
)
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import (
    ApplicationValidationError,
    ProviderUnavailableError,
)
from app.application.use_cases import (
    GenerateReplySuggestionsUseCase,
    SimplifyTranscriptSegmentUseCase,
    TranslateTranscriptSegmentUseCase,
)
from app.domain.value_objects import MeetingId


def _segment(
    *,
    transcript_id: UUID | None = None,
    text: str = "Guten Morgen",
) -> TranscriptSegment:
    return TranscriptSegment(
        transcript_id=transcript_id or uuid4(),
        meeting_id=MeetingId.new(),
        text=text,
        timestamp=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        source=AudioSource.MIXED,
        speaker="Unknown",
    )


class FakeTranslationProvider:
    """Translation fake with explicit request tracking."""

    def __init__(self, outcome: TranslationResult | Exception) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    async def translate(self, request: object) -> TranslationResult:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeSimplificationProvider:
    """Simplification fake with explicit request tracking."""

    def __init__(self, outcome: GermanSimplificationResult | Exception) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    async def simplify(self, request: object) -> GermanSimplificationResult:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeReplyProvider:
    """Reply-coaching fake with explicit request tracking."""

    def __init__(self, outcome: ReplyCoachingResult | Exception) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    async def suggest_replies(self, request: object) -> ReplyCoachingResult:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_transcript_segment_and_assist_update_are_immutable_and_validated() -> None:
    """Assist DTOs require stable identity and reject post-construction mutation."""

    segment = _segment()
    update = AssistUpdate(
        transcript_id=segment.transcript_id,
        capability=AssistCapability.TRANSLATION,
        state=AssistState.READY,
        translated_text="Merhaba",
    )

    with pytest.raises(FrozenInstanceError):
        segment.text = "Changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        update.state = AssistState.FAILED  # type: ignore[misc]
    with pytest.raises(ApplicationValidationError, match="Transcript ID"):
        AssistUpdate(  # type: ignore[arg-type]
            transcript_id="not-a-uuid",
            capability=AssistCapability.TRANSLATION,
            state=AssistState.FAILED,
        )
    with pytest.raises(ApplicationValidationError, match="requires"):
        AssistUpdate(
            transcript_id=uuid4(),
            capability=AssistCapability.TRANSLATION,
            state=AssistState.READY,
        )


def test_reply_context_limits_previous_segments_and_trims_oldest_first() -> None:
    """Reply context keeps the newest history inside the 1200-character budget."""

    previous = tuple(_segment(text=str(index) * 300) for index in range(7))
    context = ReplyContext(
        newest_finalized_segment=_segment(text="latest" * 20),
        previous_finalized_segments=previous,
    )

    assert len(context.previous_finalized_segments) <= 6
    assert len(context.conversation_text) <= 1_200
    assert context.previous_finalized_segments == previous[-3:]


def test_translate_segment_returns_ready_update_and_calls_provider_once() -> None:
    """Translation is independent and correlated to the persisted transcript UUID."""

    segment = _segment()
    provider = FakeTranslationProvider(
        TranslationResult(
            translated_text="Merhaba",
            source_language=LanguageCode(value="de"),
            target_language=LanguageCode(value="tr"),
        )
    )
    result = asyncio.run(
        TranslateTranscriptSegmentUseCase(
            provider=provider,  # type: ignore[arg-type]
            target_language=LanguageCode(value="tr"),
        ).execute(segment)
    )

    assert result == AssistUpdate(
        transcript_id=segment.transcript_id,
        capability=AssistCapability.TRANSLATION,
        state=AssistState.READY,
        translated_text="Merhaba",
    )
    assert len(provider.requests) == 1


def test_provider_failure_becomes_a_generic_failed_update() -> None:
    """Provider failures do not throw or mutate the persisted transcript boundary."""

    segment = _segment()
    provider = FakeTranslationProvider(ProviderUnavailableError("private detail"))
    result = asyncio.run(
        TranslateTranscriptSegmentUseCase(
            provider=provider,  # type: ignore[arg-type]
            target_language=LanguageCode(value="tr"),
        ).execute(segment)
    )

    assert result == AssistUpdate(
        transcript_id=segment.transcript_id,
        capability=AssistCapability.TRANSLATION,
        state=AssistState.FAILED,
    )
    assert len(provider.requests) == 1


def test_simplification_uses_requested_level_and_returns_ready_update() -> None:
    """Simplification stays independent and uses the execute-call CEFR level."""

    segment = _segment()
    provider = FakeSimplificationProvider(
        GermanSimplificationResult(
            original_text=segment.text,
            simplified_text="Guten Tag",
            target_level=GermanLevel.B1,
        )
    )
    result = asyncio.run(
        SimplifyTranscriptSegmentUseCase(provider=provider).execute(  # type: ignore[arg-type]
            segment,
            target_level=GermanLevel.B1,
        )
    )

    assert result.simplified_text == "Guten Tag"
    assert result.capability is AssistCapability.SIMPLIFICATION
    assert result.state is AssistState.READY
    assert len(provider.requests) == 1


def test_reply_coaching_limits_suggestions_to_two_and_calls_provider_once() -> None:
    """Reply coaching applies the V1 two-suggestion bound after provider output."""

    newest = _segment()
    provider = FakeReplyProvider(
        ReplyCoachingResult(
            suggestions=tuple(
                ReplySuggestion(text=f"Antwort {index}", tone=ReplyTone.PROFESSIONAL)
                for index in range(3)
            )
        )
    )
    result = asyncio.run(
        GenerateReplySuggestionsUseCase(provider=provider).execute(  # type: ignore[arg-type]
            ReplyContext(newest_finalized_segment=newest)
        )
    )

    assert result.capability is AssistCapability.REPLY_COACHING
    assert result.state is AssistState.READY
    assert result.reply_suggestions is not None
    assert len(result.reply_suggestions) == 2
    assert len(provider.requests) == 1
