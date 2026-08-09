"""Tests for explicit recording-maintenance composition-root boundaries."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingCleanupResult,
    RecordingDeletionStatus,
    RecordingKeyReference,
    RecordingMediaFormat,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.application.exceptions import RecordingKeyUnavailableError
from app.core.config import Settings
from app.core.container import Container, _create_recording_key_store
from app.domain.value_objects import MeetingId
from app.infrastructure.recordings import (
    MacOSKeychainRecordingKeyStore,
    RecordingStorageMetadataResolver,
    UnavailableRecordingKeyStore,
)
from pydantic import ValidationError


def test_recording_settings_resolve_without_creating_a_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "local-recordings"
    settings = Settings(recordings_root_directory=root)

    assert settings.recordings_root_directory == root.resolve()
    assert not root.exists()
    with pytest.raises(ValidationError):
        Settings(recording_segment_max_plaintext_bytes=0)


def test_container_owns_lightweight_adapters_and_fresh_factories(
    tmp_path: Path,
) -> None:
    container = Container(Settings(recordings_root_directory=tmp_path / "recordings"))
    with pytest.raises(RuntimeError):
        container.get_recording_storage()

    async def exercise() -> None:
        await container.start()
        assert container.get_recording_storage() is container.get_recording_storage()
        assert (
            container.get_recording_key_store() is container.get_recording_key_store()
        )
        assert (
            container.get_recording_retention_cleanup_service()
            is not container.get_recording_retention_cleanup_service()
        )
        assert (
            container.get_update_audio_retention_use_case()
            is not container.get_update_audio_retention_use_case()
        )
        assert not (tmp_path / "recordings").exists()
        await container.stop()

    asyncio.run(exercise())
    with pytest.raises(RuntimeError):
        container.get_recording_key_store()


def test_platform_key_store_selection_is_lazy_and_testable(
    tmp_path: Path,
) -> None:
    unavailable = _create_recording_key_store(platform_name="Linux")
    assert isinstance(unavailable, UnavailableRecordingKeyStore)

    with pytest.raises(RecordingKeyUnavailableError):
        asyncio.run(
            unavailable.exists(
                RecordingKeyReference(value="a" * 24),
            )
        )

    assert isinstance(
        _create_recording_key_store(platform_name="Darwin"),
        MacOSKeychainRecordingKeyStore,
    )

    linux_container = Container(
        Settings(
            recordings_root_directory=tmp_path / "linux-recordings",
        ),
        recording_key_store_factory=lambda: _create_recording_key_store(
            platform_name="Linux",
        ),
    )

    darwin_container = Container(
        Settings(
            recordings_root_directory=tmp_path / "darwin-recordings",
        ),
        recording_key_store_factory=lambda: _create_recording_key_store(
            platform_name="Darwin",
        ),
    )

    async def exercise() -> None:
        await linux_container.start()
        assert isinstance(
            linux_container.get_recording_key_store(),
            UnavailableRecordingKeyStore,
        )
        await linux_container.stop()
        await linux_container.stop()

        await darwin_container.start()
        assert isinstance(
            darwin_container.get_recording_key_store(),
            MacOSKeychainRecordingKeyStore,
        )
        await darwin_container.stop()
        await darwin_container.stop()

    asyncio.run(exercise())


def test_one_shot_methods_delegate_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Cleanup:
        def __init__(self) -> None:
            self.calls = 0

        async def run_once(self) -> RecordingCleanupResult:
            self.calls += 1
            return RecordingCleanupResult.from_items(())

    class _Reconcile:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self) -> RecordingCleanupResult:
            self.calls += 1
            return RecordingCleanupResult.from_items(())

    container = Container(Settings(recordings_root_directory=tmp_path / "recordings"))
    cleanup, reconcile = _Cleanup(), _Reconcile()
    monkeypatch.setattr(
        container,
        "get_recording_retention_cleanup_service",
        lambda: cleanup,
    )
    monkeypatch.setattr(
        container,
        "get_reconcile_recording_storage_use_case",
        lambda: reconcile,
    )

    async def exercise() -> None:
        await container.start()
        cleanup_result = await container.run_recording_retention_cleanup_once()
        reconciliation_result = (
            await container.run_recording_storage_reconciliation_once()
        )
        assert cleanup_result.checked_count == 0
        assert reconciliation_result.checked_count == 0
        await container.stop()

    asyncio.run(exercise())
    assert cleanup.calls == 1
    assert reconcile.calls == 1


def test_metadata_resolver_uses_one_short_unit_of_work() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    record = RecordingMetadataRecord(
        metadata=RecordingMetadata(
            recording_id=UUID(int=1),
            meeting_id=MeetingId(UUID(int=2)),
            state=RecordingState.COMPLETED,
            retention_policy=RecordingRetentionPolicy.SEVEN_DAYS,
            created_at=created_at,
            expires_at=created_at + timedelta(days=7),
            capture_anchor_utc=None,
            duration_seconds=None,
            protected=False,
            consent_confirmed=True,
            consent_confirmed_at=created_at,
            deletion_status=RecordingDeletionStatus.SCHEDULED,
            deleted_at=None,
            encryption_format_version=1,
            container_format=RecordingMediaFormat.LEGACY_M4A,
            segment_count=0,
            has_gaps=False,
        ),
        storage_directory_token="opaque-storage-token",
        key_reference="a" * 24,
        current_segment_index=0,
    )

    class _Repository:
        async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None:
            return record if recording_id == record.metadata.recording_id else None

    class _UnitOfWork:
        def __init__(self) -> None:
            self.recordings = _Repository()
            self.exited = False

        async def __aenter__(self) -> "_UnitOfWork":
            return self

        async def __aexit__(self, *_: object) -> None:
            self.exited = True

    unit_of_work = _UnitOfWork()
    resolver = RecordingStorageMetadataResolver(lambda: unit_of_work)
    resolved = asyncio.run(resolver.resolve(record.metadata.recording_id))

    assert resolved.storage_directory_token == record.storage_directory_token
    assert unit_of_work.exited is True
