"""Application composition root."""

import logging
from contextlib import suppress

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
from app.application.services import TranscriptDeduplicator
from app.application.use_cases import (
    AddTranscriptUseCase,
    CreateMeetingUseCase,
    EndMeetingUseCase,
    ProcessLiveAudioChunkUseCase,
    RenameMeetingUseCase,
    StartMeetingUseCase,
)
from app.core.config import Settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.audio import (
    BoundedAudioChunkBuffer,
    BufferedLiveTranscriptionSession,
)
from app.infrastructure.database.engine import create_engine_from_settings
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from app.infrastructure.providers.faster_whisper import (
    FasterWhisperModelManager,
    FasterWhisperSpeechToTextProvider,
)
from app.infrastructure.providers.ollama import (
    OllamaClient,
    OllamaGermanSimplificationProvider,
    OllamaMeetingSummarizationProvider,
    OllamaReplyCoachingProvider,
    OllamaTranslationProvider,
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
        self._ollama_client: OllamaClient | None = None
        self._ollama_translation_provider: TranslationProvider | None = None
        self._ollama_german_simplification_provider: (
            GermanSimplificationProvider | None
        ) = None
        self._ollama_reply_coaching_provider: ReplyCoachingProvider | None = None
        self._ollama_meeting_summarization_provider: (
            MeetingSummarizationProvider | None
        ) = None
        self._is_started = False
        setup_logging(settings)

    async def start(self) -> None:
        """Create lifecycle-managed persistence and provider resources."""

        if self._is_started:
            return

        engine = create_engine_from_settings(self._settings)
        self._engine = engine
        self._session_factory = create_session_factory(engine)
        try:
            self._configure_speech_to_text_provider()
            self._configure_ollama_providers()
        except Exception:
            await self._cleanup_failed_start()
            raise

        self._is_started = True

    async def stop(self) -> None:
        """Dispose lifecycle-managed persistence and provider resources."""

        model_manager = self._faster_whisper_model_manager
        try:
            if model_manager is not None:
                model_manager.close()
        finally:
            self._faster_whisper_speech_to_text_provider = None
            self._faster_whisper_model_manager = None
            try:
                try:
                    await self._dispose_ollama_resources()
                finally:
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
        """Return the active translation provider for this lifecycle."""

        self._require_started()

        managed_provider = self._ollama_translation_provider
        if managed_provider is not None:
            return managed_provider

        factory = self._translation_provider_factory
        if factory is None:
            raise ProviderUnavailableError("Translation provider is not configured.")
        return factory()

    def get_german_simplification_provider(self) -> GermanSimplificationProvider:
        """Return the active German simplification provider for this lifecycle."""

        self._require_started()

        managed_provider = self._ollama_german_simplification_provider
        if managed_provider is not None:
            return managed_provider

        factory = self._german_simplification_provider_factory
        if factory is None:
            raise ProviderUnavailableError(
                "German simplification provider is not configured."
            )
        return factory()

    def get_reply_coaching_provider(self) -> ReplyCoachingProvider:
        """Return the active reply-coaching provider for this lifecycle."""

        self._require_started()

        managed_provider = self._ollama_reply_coaching_provider
        if managed_provider is not None:
            return managed_provider

        factory = self._reply_coaching_provider_factory
        if factory is None:
            raise ProviderUnavailableError("Reply-coaching provider is not configured.")
        return factory()

    def get_meeting_summarization_provider(self) -> MeetingSummarizationProvider:
        """Return the active meeting-summarization provider for this lifecycle."""

        self._require_started()

        managed_provider = self._ollama_meeting_summarization_provider
        if managed_provider is not None:
            return managed_provider

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

    def get_add_transcript_use_case(self) -> AddTranscriptUseCase:
        """Create a Unit-of-Work-backed transcript addition use case."""

        self._require_session_factory()
        return AddTranscriptUseCase(self.get_unit_of_work)

    def get_process_live_audio_chunk_use_case(
        self,
    ) -> ProcessLiveAudioChunkUseCase:
        """Create a live-audio chunk processor from active application dependencies."""

        self._require_session_factory()
        return ProcessLiveAudioChunkUseCase(
            speech_to_text_provider=self.get_speech_to_text_provider(),
            transcript_deduplicator=TranscriptDeduplicator(),
            add_transcript_use_case=self.get_add_transcript_use_case(),
        )

    def get_live_transcription_session(self) -> BufferedLiveTranscriptionSession:
        """Create one independent buffered live-transcription session."""

        self._require_session_factory()
        return BufferedLiveTranscriptionSession(
            buffer=BoundedAudioChunkBuffer(max_size=3),
            processor=self.get_process_live_audio_chunk_use_case(),
        )

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

    def _configure_ollama_providers(self) -> None:
        """Configure selected Ollama capability adapters for this lifecycle."""

        selected_capabilities = (
            (
                "translation",
                self._settings.translation_provider,
                self._translation_provider_factory,
            ),
            (
                "German simplification",
                self._settings.german_simplification_provider,
                self._german_simplification_provider_factory,
            ),
            (
                "reply coaching",
                self._settings.reply_coaching_provider,
                self._reply_coaching_provider_factory,
            ),
            (
                "meeting summarization",
                self._settings.meeting_summarization_provider,
                self._meeting_summarization_provider_factory,
            ),
        )
        automatic_capabilities = [
            (capability, provider_name)
            for capability, provider_name, factory in selected_capabilities
            if factory is None and provider_name != "unconfigured"
        ]

        for capability, provider_name in automatic_capabilities:
            if provider_name != "ollama":
                raise ProviderUnavailableError(
                    f"Unsupported {capability} provider: {provider_name}."
                )

        if not automatic_capabilities:
            return

        client = OllamaClient(
            base_url=self._settings.ollama_base_url,
            request_timeout_seconds=self._settings.ollama_request_timeout_seconds,
            temperature=self._settings.ollama_temperature,
            context_length=self._settings.ollama_context_length,
            keep_alive=self._settings.ollama_keep_alive,
        )
        self._ollama_client = client

        if self._translation_provider_factory is None and (
            self._settings.translation_provider == "ollama"
        ):
            self._ollama_translation_provider = OllamaTranslationProvider(
                client=client,
                model=self._settings.ollama_translation_model,
            )
        if self._german_simplification_provider_factory is None and (
            self._settings.german_simplification_provider == "ollama"
        ):
            self._ollama_german_simplification_provider = (
                OllamaGermanSimplificationProvider(
                    client=client,
                    model=self._settings.ollama_german_simplification_model,
                )
            )
        if self._reply_coaching_provider_factory is None and (
            self._settings.reply_coaching_provider == "ollama"
        ):
            self._ollama_reply_coaching_provider = OllamaReplyCoachingProvider(
                client=client,
                model=self._settings.ollama_reply_coaching_model,
            )
        if self._meeting_summarization_provider_factory is None and (
            self._settings.meeting_summarization_provider == "ollama"
        ):
            self._ollama_meeting_summarization_provider = (
                OllamaMeetingSummarizationProvider(
                    client=client,
                    model=self._settings.ollama_meeting_summarization_model,
                )
            )

    async def _cleanup_failed_start(self) -> None:
        """Release partially created resources while preserving the startup error."""

        try:
            with suppress(Exception):
                await self._dispose_ollama_resources()
            model_manager = self._faster_whisper_model_manager
            if model_manager is not None:
                with suppress(Exception):
                    model_manager.close()
        finally:
            self._faster_whisper_speech_to_text_provider = None
            self._faster_whisper_model_manager = None
            self._dispose_persistence_resources()

    async def _dispose_ollama_resources(self) -> None:
        """Close the shared Ollama client and clear its lifecycle-owned adapters."""

        client = self._ollama_client
        try:
            if client is not None:
                await client.close()
        finally:
            self._ollama_translation_provider = None
            self._ollama_german_simplification_provider = None
            self._ollama_reply_coaching_provider = None
            self._ollama_meeting_summarization_provider = None
            self._ollama_client = None

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
