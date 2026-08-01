"""Opt-in smoke tests for local Ollama capability providers."""

import asyncio
import os
from collections.abc import Awaitable, Callable

import pytest
from app.application.dto.ai import (
    GermanLevel,
    GermanSimplificationRequest,
    GermanSimplificationResult,
    LanguageCode,
    MeetingSummaryRequest,
    MeetingSummaryResult,
    ReplyCoachingRequest,
    ReplyCoachingResult,
    ReplyTone,
    TranslationRequest,
    TranslationResult,
)
from app.application.exceptions import ProviderUnavailableError
from app.core.config import Settings
from app.core.container import Container


def _require_local_ai_enabled() -> None:
    """Skip unless a developer explicitly enables local AI inference."""

    if os.environ.get("AI_MEETING_COPILOT_RUN_LOCAL_AI_TESTS") != "1":
        pytest.skip(
            "Set AI_MEETING_COPILOT_RUN_LOCAL_AI_TESTS=1 to enable local AI tests."
        )


def _ollama_settings() -> Settings:
    """Create settings that select all local Ollama capability adapters."""

    return Settings(
        translation_provider="ollama",
        german_simplification_provider="ollama",
        reply_coaching_provider="ollama",
        meeting_summarization_provider="ollama",
    )


async def _run_with_ollama_container[ResultT](
    operation: Callable[[Container], Awaitable[ResultT]],
) -> ResultT:
    """Run one operation through the public container and always stop it."""

    container = Container(_ollama_settings())
    try:
        await container.start()
        return await operation(container)
    except ProviderUnavailableError:
        pytest.skip(
            "Local Ollama is unavailable or a configured model has not been pulled."
        )
    finally:
        await container.stop()


@pytest.mark.local_ai
def test_local_ollama_translation() -> None:
    """Translate concise synthetic German text to Turkish with local Ollama."""

    _require_local_ai_enabled()

    async def translate(container: Container) -> TranslationResult:
        return await container.get_translation_provider().translate(
            TranslationRequest(
                text="Guten Morgen.",
                source_language=LanguageCode(value="de"),
                target_language=LanguageCode(value="tr"),
            )
        )

    result = asyncio.run(_run_with_ollama_container(translate))

    assert result.translated_text.strip()
    assert result.source_language == LanguageCode(value="de")
    assert result.target_language == LanguageCode(value="tr")


@pytest.mark.local_ai
def test_local_ollama_german_simplification() -> None:
    """Simplify concise synthetic German text through local Ollama."""

    _require_local_ai_enabled()
    request = GermanSimplificationRequest(
        text="Bitte bestätigen Sie den Termin bis morgen.",
        target_level=GermanLevel.B1,
    )

    async def simplify(container: Container) -> GermanSimplificationResult:
        return await container.get_german_simplification_provider().simplify(request)

    result = asyncio.run(_run_with_ollama_container(simplify))

    assert result.simplified_text.strip()
    assert result.original_text == request.text
    assert result.target_level is GermanLevel.B1


@pytest.mark.local_ai
def test_local_ollama_reply_coaching() -> None:
    """Generate bounded professional German B1 replies with local Ollama."""

    _require_local_ai_enabled()
    request = ReplyCoachingRequest(
        conversation_context="Ein Team plant einen Termin für die nächste Woche.",
        latest_utterance="Können Sie am Dienstag teilnehmen?",
        response_language=LanguageCode(value="de"),
        target_german_level=GermanLevel.B1,
        tone=ReplyTone.PROFESSIONAL,
        max_suggestions=2,
    )

    async def suggest_replies(container: Container) -> ReplyCoachingResult:
        return await container.get_reply_coaching_provider().suggest_replies(request)

    result = asyncio.run(_run_with_ollama_container(suggest_replies))

    assert 1 <= len(result.suggestions) <= request.max_suggestions
    assert all(suggestion.text.strip() for suggestion in result.suggestions)


@pytest.mark.local_ai
def test_local_ollama_meeting_summarization() -> None:
    """Summarize a concise synthetic meeting transcript with local Ollama."""

    _require_local_ai_enabled()
    request = MeetingSummaryRequest(
        meeting_name="Release-Planung",
        transcript_text=(
            "Mira bestätigt den Release am Freitag. "
            "Alex erstellt die Release Notes. "
            "Die Uhrzeit für das Deployment ist noch offen."
        ),
        language=LanguageCode(value="de"),
    )

    async def summarize(container: Container) -> MeetingSummaryResult:
        return await container.get_meeting_summarization_provider().summarize(request)

    result = asyncio.run(_run_with_ollama_container(summarize))

    assert result.summary.strip()
    assert all(decision.strip() for decision in result.key_decisions)
    assert all(item.text.strip() for item in result.action_items)
    assert all(
        item.assignee is None or item.assignee.strip() for item in result.action_items
    )
    assert all(question.strip() for question in result.open_questions)
