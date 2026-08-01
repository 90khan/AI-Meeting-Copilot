"""Application composition root."""

import logging

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.exceptions import ProviderUnavailableError
from app.application.interfaces import (
    GermanSimplificationProvider,
    GermanSimplificationProviderFactory,
    MeetingSummarizationProvider,
    MeetingSummarizationProviderFactory,
    ReplyCoachingProvider,
    ReplyCoachingProviderFactory,
    SpeechToTextProvider,
    SpeechToTextProviderFactory,
    TranslationProvider,
    TranslationProviderFactory,
    UnitOfWork,
)
from app.application.use_cases import (
    CreateMeetingUseCase,
    EndMeetingUseCase,
    RenameMeetingUseCase,
    StartMeetingUseCase,
)
from app.core.config import Settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.database.engine import create_engine_from_settings
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from app.infrastructure.providers.faster_whisper import (
    FasterWhisperModelManager,
    FasterWhisperSpeechToTextProvider,
)


class Container:
    """Explicitly wire application dependencies and manage their lifecycle."""

    def __init__(self, settings: Settings) -> None:
        """Create a container using the supplied immutable application settings."""

        self._settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._speech_to_text_provider_factory: SpeechToTextProviderFactory | None = None
        self._translation_provider_factory: TranslationProviderFactory | None = None
        self._german_simplification_provider_factory: (
            GermanSimplificationProviderFactory | None
        ) = None
        self._reply_coaching_provider_factory: ReplyCoachingProviderFactory | None = (
            None
        )
        self._meeting_summarization_provider_factory: (
            MeetingSummarizationProviderFactory | None
        ) = None
        self._faster_whisper_model_manager: FasterWhisperModelManager | None = None
        self._faster_whisper_speech_to_text_provider: SpeechToTextProvider | None = None
        self._is_started = False
        setup_logging(settings)

    async def start(self) -> None:
        """Create lifecycle-managed persistence and speech-provider resources."""

        if self._is_started:
            return

        engine = create_engine_from_settings(self._settings)
        self._engine = engine
        self._session_factory = create_session_factory(engine)
        try:
            self._configure_speech_to_text_provider()
        except Exception:
            self._dispose_persistence_resources()
            raise

        self._is_started = True

    async def stop(self) -> None:
        """Dispose lifecycle-managed persistence and speech-provider resources."""

        model_manager = self._faster_whisper_model_manager
        try:
            if model_manager is not None:
                model_manager.close()
        finally:
            self._faster_whisper_speech_to_text_provider = None
            self._faster_whisper_model_manager = None
            try:
                self._dispose_persistence_resources()
            finally:
                self._is_started = False

    def get_settings(self) -> Settings:
        """Return the container's immutable application settings."""

        return self._settings

    def get_logger(self, name: str) -> logging.Logger:
        """Return a module-qualified logger configured by this container."""

        return get_logger(name)

    def register_speech_to_text_provider_factory(
        self, factory: SpeechToTextProviderFactory
    ) -> None:
        """Register the factory used to resolve speech-to-text providers."""

        self._require_ai_factories_mutable()
        self._speech_to_text_provider_factory = factory

    def register_translation_provider_factory(
        self, factory: TranslationProviderFactory
    ) -> None:
        """Register the factory used to resolve translation providers."""

        self._require_ai_factories_mutable()
        self._translation_provider_factory = factory

    def register_german_simplification_provider_factory(
        self, factory: GermanSimplificationProviderFactory
    ) -> None:
        """Register the factory used to resolve German simplification providers."""

        self._require_ai_factories_mutable()
        self._german_simplification_provider_factory = factory

    def register_reply_coaching_provider_factory(
        self, factory: ReplyCoachingProviderFactory
    ) -> None:
        """Register the factory used to resolve reply-coaching providers."""

        self._require_ai_factories_mutable()
        self._reply_coaching_provider_factory = factory

    def register_meeting_summarization_provider_factory(
        self, factory: MeetingSummarizationProviderFactory
    ) -> None:
        """Register the factory used to resolve meeting-summarization providers."""

        self._require_ai_factories_mutable()
        self._meeting_summarization_provider_factory = factory

    def get_speech_to_text_provider(self) -> SpeechToTextProvider:
        """Return the active speech-to-text provider for this lifecycle."""

        self._require_started()

        managed_provider = self._faster_whisper_speech_to_text_provider
        if managed_provider is not None:
            return managed_provider

        factory = self._speech_to_text_provider_factory
        if factory is None:
            raise ProviderUnavailableError("Speech-to-text provider is not configured.")
        return factory()

    def get_translation_provider(self) -> TranslationProvider:
        """Create a translation provider from its registered factory."""

        factory = self._translation_provider_factory
        if factory is None:
            raise ProviderUnavailableError("Translation provider is not configured.")
        return factory()

    def get_german_simplification_provider(self) -> GermanSimplificationProvider:
        """Create a German simplification provider from its registered factory."""

        factory = self._german_simplification_provider_factory
        if factory is None:
            raise ProviderUnavailableError(
                "German simplification provider is not configured."
            )
        return factory()

    def get_reply_coaching_provider(self) -> ReplyCoachingProvider:
        """Create a reply-coaching provider from its registered factory."""

        factory = self._reply_coaching_provider_factory
        if factory is None:
            raise ProviderUnavailableError("Reply-coaching provider is not configured.")
        return factory()

    def get_meeting_summarization_provider(self) -> MeetingSummarizationProvider:
        """Create a meeting-summarization provider from its registered factory."""

        factory = self._meeting_summarization_provider_factory
        if factory is None:
            raise ProviderUnavailableError(
                "Meeting summarization provider is not configured."
            )
        return factory()

    def get_unit_of_work(self) -> UnitOfWork:
        """Create an independent Unit of Work from active persistence resources."""

        return SQLAlchemyUnitOfWork(self._require_session_factory())

    def get_create_meeting_use_case(self) -> CreateMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting creation use case."""

        self._require_session_factory()
        return CreateMeetingUseCase(self.get_unit_of_work)

    def get_start_meeting_use_case(self) -> StartMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting start use case."""

        self._require_session_factory()
        return StartMeetingUseCase(self.get_unit_of_work)

    def get_rename_meeting_use_case(self) -> RenameMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting rename use case."""

        self._require_session_factory()
        return RenameMeetingUseCase(self.get_unit_of_work)

    def get_end_meeting_use_case(self) -> EndMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting end use case."""

        self._require_session_factory()
        return EndMeetingUseCase(self.get_unit_of_work)

    def _require_session_factory(self) -> sessionmaker[Session]:
        """Return the active session factory or raise a lifecycle error."""

        if self._session_factory is None:
            raise RuntimeError("Container has not been started.")

        return self._session_factory

    def _require_started(self) -> None:
        """Raise when a lifecycle-owned dependency is resolved while stopped."""

        if not self._is_started:
            raise RuntimeError("Container has not been started.")

    def _configure_speech_to_text_provider(self) -> None:
        """Configure the selected local speech provider for this lifecycle."""

        if self._speech_to_text_provider_factory is not None:
            return

        provider_name = self._settings.speech_to_text_provider
        if provider_name == "unconfigured":
            return
        if provider_name != "faster-whisper":
            raise ProviderUnavailableError(
                f"Unsupported speech-to-text provider: {provider_name}."
            )

        model_manager = FasterWhisperModelManager(
            model_name=self._settings.faster_whisper_model,
            device=self._settings.faster_whisper_device,
            compute_type=self._settings.faster_whisper_compute_type,
            cpu_threads=self._settings.faster_whisper_cpu_threads,
            download_directory=self._settings.faster_whisper_download_directory,
        )
        self._faster_whisper_model_manager = model_manager
        self._faster_whisper_speech_to_text_provider = (
            FasterWhisperSpeechToTextProvider(
                model_manager=model_manager,
                beam_size=self._settings.faster_whisper_beam_size,
                vad_enabled=self._settings.faster_whisper_vad_enabled,
            )
        )

    def _dispose_persistence_resources(self) -> None:
        """Dispose persistence resources without affecting provider registrations."""

        engine = self._engine
        try:
            if engine is not None:
                engine.dispose()
        finally:
            self._engine = None
            self._session_factory = None

    def _require_ai_factories_mutable(self) -> None:
        """Reject provider-factory changes while the container is running."""

        if self._engine is not None:
            raise RuntimeError(
                "AI provider factories cannot change after container start."
            )
