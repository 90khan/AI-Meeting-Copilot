"""Generate an in-memory, versioned Turkish Meeting translation artifact."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.application.dto.ai import LanguageCode, TranslationRequest
from app.application.dto.meeting_review import (
    GenerateMeetingTranslationCommand,
    GenerateMeetingTranslationResult,
    MeetingDetail,
    MeetingTranslationArtifact,
    TranslationArtifactSegment,
    TranslationArtifactStatus,
)
from app.application.interfaces import TranslationProvider, UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingStatus

_SOURCE_LANGUAGE = LanguageCode(value="de")
_TARGET_LANGUAGE = LanguageCode(value="tr")


class GenerateMeetingTranslationUseCase:
    """Translate a complete ended Meeting transcript without persistence effects."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        translation_provider: TranslationProvider,
        utc_clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        provider_name: str,
        model_name: str,
        prompt_version: str,
        schema_version: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._translation_provider = translation_provider
        self._utc_clock = utc_clock
        self._uuid_factory = uuid_factory
        self._provider_name = provider_name
        self._model_name = model_name
        self._prompt_version = prompt_version
        self._schema_version = schema_version

    async def execute(
        self,
        command: GenerateMeetingTranslationCommand,
    ) -> GenerateMeetingTranslationResult:
        """Generate or reuse an artifact for the complete ended Meeting transcript."""

        async with self._unit_of_work_factory() as unit_of_work:
            detail = await unit_of_work.meeting_reviews.get_meeting_detail(
                command.meeting_id
            )
            if detail is None:
                raise LookupError("Meeting not found")
            if detail.status is not MeetingStatus.ENDED:
                raise InvalidStateTransitionError(
                    "Meeting must be ended before generating a translation."
                )

            existing = await unit_of_work.meeting_translations.get_latest_completed(
                command.meeting_id
            )
            if not command.force_regenerate and self._can_reuse(existing, detail):
                assert existing is not None
                return GenerateMeetingTranslationResult(
                    artifact=existing,
                    reused_existing=True,
                )

            latest_version = await unit_of_work.meeting_translations.get_latest_version(
                command.meeting_id,
                target_language=str(_TARGET_LANGUAGE),
            )
            segments = await self._translate_segments(detail)
            completed_at = self._utc_clock()
            artifact = MeetingTranslationArtifact(
                artifact_id=self._uuid_factory(),
                meeting_id=command.meeting_id,
                version=(latest_version or 0) + 1,
                target_language=str(_TARGET_LANGUAGE),
                status=TranslationArtifactStatus.COMPLETED,
                created_at=completed_at,
                completed_at=completed_at,
                source_transcript_count=len(detail.transcript),
                segments=segments,
                provider_name=self._provider_name,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
                schema_version=self._schema_version,
                failure_code=None,
            )
            return GenerateMeetingTranslationResult(
                artifact=artifact,
                reused_existing=False,
            )

    async def _translate_segments(
        self,
        detail: MeetingDetail,
    ) -> tuple[TranslationArtifactSegment, ...]:
        segments: list[TranslationArtifactSegment] = []
        for transcript in detail.transcript:
            result = await self._translation_provider.translate(
                TranslationRequest(
                    text=transcript.text,
                    source_language=_SOURCE_LANGUAGE,
                    target_language=_TARGET_LANGUAGE,
                    preserve_formatting=True,
                )
            )
            segments.append(
                TranslationArtifactSegment(
                    transcript_id=transcript.transcript_id,
                    source_text=transcript.text,
                    translated_text=result.translated_text,
                )
            )
        return tuple(segments)

    @staticmethod
    def _can_reuse(
        artifact: MeetingTranslationArtifact | None,
        detail: MeetingDetail,
    ) -> bool:
        if artifact is None or artifact.source_transcript_count != len(
            detail.transcript
        ):
            return False
        return all(
            artifact_segment.transcript_id == transcript.transcript_id
            and artifact_segment.source_text == transcript.text
            for artifact_segment, transcript in zip(
                artifact.segments,
                detail.transcript,
                strict=True,
            )
        )
