"""Tests for application container persistence wiring."""

import asyncio

import pytest
from app.application.dto import AudioSource
from app.application.dto.ai import LanguageCode, TranslationRequest, TranslationResult
from app.application.use_cases import (
    AddTranscriptUseCase,
    CreateMeetingUseCase,
    EndMeetingUseCase,
    GenerateMeetingReviewUseCase,
    GenerateMeetingTranslationUseCase,
    GetMeetingReviewUseCase,
    GetMeetingTranslationUseCase,
    ProcessLiveAudioChunkUseCase,
    RenameMeetingUseCase,
    StartLiveTranscriptionSessionUseCase,
    StartMeetingUseCase,
)
from app.core.config import Settings
from app.core.container import Container
from app.domain.value_objects import MeetingId
from app.infrastructure.audio import BufferedLiveTranscriptionSession


class _FakeTranslationProvider:
    """Translation fake that records whether startup or factories invoke it."""

    def __init__(self) -> None:
        self.calls = 0

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.calls += 1
        return TranslationResult(
            translated_text=request.text,
            source_language=request.source_language or LanguageCode(value="de"),
            target_language=request.target_language,
        )


def test_start_creates_an_engine_and_session_factory() -> None:
    """Starting the container initializes persistence resources once."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    asyncio.run(container.start())

    assert container._engine is not None
    assert container._session_factory is not None

    asyncio.run(container.stop())


def test_repeated_start_reuses_existing_persistence_resources() -> None:
    """Starting an active container is idempotent."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    asyncio.run(container.start())
    engine = container._engine
    session_factory = container._session_factory
    asyncio.run(container.start())

    assert container._engine is engine
    assert container._session_factory is session_factory

    asyncio.run(container.stop())


def test_stop_disposes_and_clears_persistence_resources() -> None:
    """Stopping clears engine and session-factory references."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    asyncio.run(container.start())

    asyncio.run(container.stop())

    assert container._engine is None
    assert container._session_factory is None


def test_repeated_stop_is_safe() -> None:
    """Stopping an already stopped container is idempotent."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    asyncio.run(container.stop())
    asyncio.run(container.stop())

    assert container._engine is None
    assert container._session_factory is None


def test_unit_of_work_access_before_start_raises_runtime_error() -> None:
    """Persistence factories are unavailable before the container starts."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_unit_of_work()


def test_unit_of_work_factory_returns_independent_instances() -> None:
    """Each Unit of Work factory call creates a separate work-unit object."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    asyncio.run(container.start())

    first_unit_of_work = container.get_unit_of_work()
    second_unit_of_work = container.get_unit_of_work()

    assert first_unit_of_work is not second_unit_of_work

    asyncio.run(container.stop())


def test_containers_do_not_share_persistence_resources() -> None:
    """Container instances own distinct engine and session-factory state."""

    first_container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    second_container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    asyncio.run(first_container.start())
    asyncio.run(second_container.start())

    assert first_container._engine is not second_container._engine
    assert first_container._session_factory is not second_container._session_factory

    asyncio.run(first_container.stop())
    asyncio.run(second_container.stop())


def test_use_case_factories_return_working_use_cases_after_start() -> None:
    """Started containers wire use cases to the Unit of Work factory."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    asyncio.run(container.start())

    assert isinstance(container.get_create_meeting_use_case(), CreateMeetingUseCase)
    assert isinstance(container.get_add_transcript_use_case(), AddTranscriptUseCase)
    assert isinstance(container.get_start_meeting_use_case(), StartMeetingUseCase)
    assert isinstance(container.get_rename_meeting_use_case(), RenameMeetingUseCase)
    assert isinstance(container.get_end_meeting_use_case(), EndMeetingUseCase)
    assert isinstance(
        container.get_start_live_transcription_session_use_case(),
        StartLiveTranscriptionSessionUseCase,
    )
    assert isinstance(
        container.get_get_meeting_translation_use_case(),
        GetMeetingTranslationUseCase,
    )
    assert isinstance(
        container.get_get_meeting_review_use_case(),
        GetMeetingReviewUseCase,
    )

    asyncio.run(container.stop())


def test_use_case_factories_raise_before_start() -> None:
    """Use cases cannot be wired before persistence resources are available."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))

    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_create_meeting_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_add_transcript_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_process_live_audio_chunk_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_live_transcription_session(
            meeting_id=MeetingId.new(),
            language_hint=None,
            source=AudioSource.MIXED,
        )
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_start_live_transcription_session_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_sidecar_token_validator()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_start_meeting_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_rename_meeting_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_end_meeting_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_generate_meeting_translation_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_get_meeting_translation_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_generate_meeting_review_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_get_meeting_review_use_case()


def test_translation_factories_are_fresh_and_do_not_call_provider_at_startup() -> None:
    """Translation composition stays lazy and injects stable generation metadata."""

    providers: list[_FakeTranslationProvider] = []
    container = Container(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            translation_provider="test-local",
            ollama_translation_model="translation-model",
        )
    )

    def factory() -> _FakeTranslationProvider:
        provider = _FakeTranslationProvider()
        providers.append(provider)
        return provider

    container.register_translation_provider_factory(factory)
    asyncio.run(container.start())

    try:
        assert providers == []
        first_generator = container.get_generate_meeting_translation_use_case()
        second_generator = container.get_generate_meeting_translation_use_case()
        first_reader = container.get_get_meeting_translation_use_case()
        second_reader = container.get_get_meeting_translation_use_case()

        assert first_generator is not second_generator
        assert isinstance(first_generator, GenerateMeetingTranslationUseCase)
        assert first_reader is not second_reader
        assert len(providers) == 2
        assert all(provider.calls == 0 for provider in providers)
        assert first_generator._provider_name == "test-local"
        assert first_generator._model_name == "translation-model"
        assert first_generator._prompt_version == "meeting_translation_v1"
        assert first_generator._schema_version == 1
    finally:
        asyncio.run(container.stop())


def test_review_factories_are_fresh_lazy_and_inject_stable_metadata() -> None:
    """Review composition reuses one lazy local client without startup generation."""

    container = Container(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            ollama_meeting_summarization_model="review-model",
        )
    )
    asyncio.run(container.start())

    try:
        assert container._ollama_client is None
        assert container._ollama_meeting_review_generation_provider is None

        first_generator = container.get_generate_meeting_review_use_case()
        second_generator = container.get_generate_meeting_review_use_case()
        first_reader = container.get_get_meeting_review_use_case()
        second_reader = container.get_get_meeting_review_use_case()

        assert isinstance(first_generator, GenerateMeetingReviewUseCase)
        assert first_generator is not second_generator
        assert isinstance(first_reader, GetMeetingReviewUseCase)
        assert first_reader is not second_reader
        assert first_generator._provider_name == "ollama"
        assert first_generator._model_name == "review-model"
        assert first_generator._prompt_version == "meeting_review_workflow_v1"
        assert first_generator._schema_version == 1
        assert container._ollama_client is not None
    finally:
        asyncio.run(container.stop())


def test_live_audio_chunk_factory_works_with_registered_speech_provider() -> None:
    """A started container wires the live-audio use case through its STT resolver."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    container.register_speech_to_text_provider_factory(lambda: object())  # type: ignore[arg-type]
    asyncio.run(container.start())

    try:
        assert isinstance(
            container.get_process_live_audio_chunk_use_case(),
            ProcessLiveAudioChunkUseCase,
        )
    finally:
        asyncio.run(container.stop())


def test_live_transcription_session_factory_returns_independent_sessions() -> None:
    """Each resolver call creates a new active live-transcription session."""

    container = Container(Settings(database_url="sqlite+pysqlite:///:memory:"))
    container.register_speech_to_text_provider_factory(lambda: object())  # type: ignore[arg-type]
    asyncio.run(container.start())

    try:
        meeting_id = MeetingId.new()
        language_hint = LanguageCode(value="de-DE")
        first_session = container.get_live_transcription_session(
            meeting_id=meeting_id,
            language_hint=language_hint,
            source=AudioSource.SYSTEM_AUDIO,
        )
        second_session = container.get_live_transcription_session(
            meeting_id=meeting_id,
            language_hint=language_hint,
            source=AudioSource.SYSTEM_AUDIO,
        )

        assert isinstance(first_session, BufferedLiveTranscriptionSession)
        assert isinstance(second_session, BufferedLiveTranscriptionSession)
        assert first_session is not second_session
        assert first_session.meeting_id == meeting_id
        assert first_session.language_hint == language_hint
        assert first_session.source is AudioSource.SYSTEM_AUDIO
    finally:
        asyncio.run(container.stop())
