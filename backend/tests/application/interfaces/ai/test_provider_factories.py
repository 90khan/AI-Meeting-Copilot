"""Tests for AI provider factory contracts."""

from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    GermanLevel,
    GermanSimplificationRequest,
    GermanSimplificationResult,
    LanguageCode,
    MeetingSummaryRequest,
    MeetingSummaryResult,
    ReplyCoachingRequest,
    ReplyCoachingResult,
    ReplySuggestion,
    ReplyTone,
    SpeechToTextRequest,
    TranscriptionResult,
    TranslationRequest,
    TranslationResult,
)
from app.application.interfaces.ai import (
    GermanSimplificationProviderFactory,
    MeetingSummarizationProviderFactory,
    ReplyCoachingProviderFactory,
    SpeechToTextProviderFactory,
    TranslationProviderFactory,
)


class FakeSpeechToTextProvider:
    """Minimal speech-to-text provider used by factory tests."""

    async def transcribe(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Return a valid empty transcription."""

        return TranscriptionResult(
            language=request.language_hint or LanguageCode(value="en"),
            segments=(),
            duration_seconds=0.0,
        )


class FakeTranslationProvider:
    """Minimal translation provider used by factory tests."""

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Return a valid predictable translation."""

        return TranslationResult(
            translated_text=request.text,
            source_language=request.source_language or LanguageCode(value="de"),
            target_language=request.target_language,
        )


class FakeGermanSimplificationProvider:
    """Minimal German simplification provider used by factory tests."""

    async def simplify(
        self, request: GermanSimplificationRequest
    ) -> GermanSimplificationResult:
        """Return a valid predictable simplification."""

        return GermanSimplificationResult(
            original_text=request.text,
            simplified_text=request.text,
            target_level=request.target_level,
        )


class FakeReplyCoachingProvider:
    """Minimal reply-coaching provider used by factory tests."""

    async def suggest_replies(
        self, request: ReplyCoachingRequest
    ) -> ReplyCoachingResult:
        """Return one valid predictable suggestion."""

        return ReplyCoachingResult(
            suggestions=(
                ReplySuggestion(text=request.latest_utterance, tone=ReplyTone.NEUTRAL),
            )
        )


class FakeMeetingSummarizationProvider:
    """Minimal summarization provider used by factory tests."""

    async def summarize(self, request: MeetingSummaryRequest) -> MeetingSummaryResult:
        """Return a valid predictable meeting summary."""

        return MeetingSummaryResult(
            summary=request.transcript_text,
            key_decisions=(),
            action_items=(),
            open_questions=(),
        )


def test_provider_factories_are_structurally_satisfiable() -> None:
    """Plain callables satisfy each runtime-checkable factory protocol."""

    def speech_factory() -> FakeSpeechToTextProvider:
        return FakeSpeechToTextProvider()

    def translation_factory() -> FakeTranslationProvider:
        return FakeTranslationProvider()

    def simplification_factory() -> FakeGermanSimplificationProvider:
        return FakeGermanSimplificationProvider()

    def reply_coaching_factory() -> FakeReplyCoachingProvider:
        return FakeReplyCoachingProvider()

    def summarization_factory() -> FakeMeetingSummarizationProvider:
        return FakeMeetingSummarizationProvider()

    assert isinstance(speech_factory, SpeechToTextProviderFactory)
    assert isinstance(translation_factory, TranslationProviderFactory)
    assert isinstance(simplification_factory, GermanSimplificationProviderFactory)
    assert isinstance(reply_coaching_factory, ReplyCoachingProviderFactory)
    assert isinstance(summarization_factory, MeetingSummarizationProviderFactory)


def test_factory_protocols_accept_structural_callables() -> None:
    """Factories can be assigned without nominal inheritance."""

    speech_factory: SpeechToTextProviderFactory = FakeSpeechToTextProvider
    translation_factory: TranslationProviderFactory = FakeTranslationProvider
    simplification_factory: GermanSimplificationProviderFactory = (
        FakeGermanSimplificationProvider
    )
    reply_coaching_factory: ReplyCoachingProviderFactory = FakeReplyCoachingProvider
    summarization_factory: MeetingSummarizationProviderFactory = (
        FakeMeetingSummarizationProvider
    )

    assert isinstance(speech_factory(), FakeSpeechToTextProvider)
    assert isinstance(translation_factory(), FakeTranslationProvider)
    assert isinstance(simplification_factory(), FakeGermanSimplificationProvider)
    assert isinstance(reply_coaching_factory(), FakeReplyCoachingProvider)
    assert isinstance(summarization_factory(), FakeMeetingSummarizationProvider)


def test_factory_test_types_use_public_dtos() -> None:
    """Factory test providers depend only on public application contracts."""

    audio = AudioInput(
        data=b"audio",
        sample_rate_hz=16_000,
        channels=1,
        audio_format=AudioFormat.WAV,
    )

    assert audio.sample_rate_hz == 16_000
    assert GermanLevel.B1 == "b1"
