"""Tests for application container persistence wiring."""

import asyncio

import pytest
from app.application.use_cases import (
    AddTranscriptUseCase,
    CreateMeetingUseCase,
    EndMeetingUseCase,
    ProcessLiveAudioChunkUseCase,
    RenameMeetingUseCase,
    StartMeetingUseCase,
)
from app.core.config import Settings
from app.core.container import Container
from app.infrastructure.audio import BufferedLiveTranscriptionSession


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
        container.get_live_transcription_session()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_start_meeting_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_rename_meeting_use_case()
    with pytest.raises(RuntimeError, match="has not been started"):
        container.get_end_meeting_use_case()


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
        first_session = container.get_live_transcription_session()
        second_session = container.get_live_transcription_session()

        assert isinstance(first_session, BufferedLiveTranscriptionSession)
        assert isinstance(second_session, BufferedLiveTranscriptionSession)
        assert first_session is not second_session
    finally:
        asyncio.run(container.stop())
