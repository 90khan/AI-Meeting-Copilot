"""Infrastructure-private short-lived lookup for opaque recording storage metadata."""

from uuid import UUID

from app.application.dto.recordings import RecordingMetadataRecord
from app.application.exceptions import RecordingStorageUnavailableError
from app.application.interfaces import UnitOfWorkFactory


class RecordingStorageMetadataResolver:
    """Resolve a known recording through one short Unit of Work."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def resolve(self, recording_id: UUID) -> RecordingMetadataRecord:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(recording_id)
        if record is None:
            raise RecordingStorageUnavailableError()
        return record
