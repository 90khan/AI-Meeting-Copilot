"""Load completed Turkish Meeting translation artifacts for public read access."""

from app.application.dto.meeting_review import (
    GetMeetingTranslationQuery,
    GetMeetingTranslationResult,
    TranslationArtifactStatus,
)
from app.application.interfaces import UnitOfWorkFactory

_TARGET_LANGUAGE = "tr"


class GetMeetingTranslationUseCase:
    """Read only completed translation artifacts through a short-lived work unit."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        query: GetMeetingTranslationQuery,
    ) -> GetMeetingTranslationResult:
        """Return the latest or requested completed artifact, if public-readable."""

        async with self._unit_of_work_factory() as unit_of_work:
            if query.version is None:
                artifact = await unit_of_work.meeting_translations.get_latest_completed(
                    query.meeting_id
                )
            else:
                artifact = (
                    await unit_of_work.meeting_translations.get_by_meeting_and_version(
                        query.meeting_id,
                        target_language=_TARGET_LANGUAGE,
                        version=query.version,
                    )
                )

        if (
            artifact is None
            or artifact.status is not TranslationArtifactStatus.COMPLETED
        ):
            raise LookupError("Meeting translation not found")
        return GetMeetingTranslationResult(artifact=artifact)
