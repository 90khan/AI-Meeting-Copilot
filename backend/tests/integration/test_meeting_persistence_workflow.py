"""End-to-end persistence workflow tests for Meeting application operations."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.application.dto import (
    CreateMeetingCommand,
    EndMeetingCommand,
    RenameMeetingCommand,
    StartMeetingCommand,
)
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.domain.value_objects import MeetingStatus


def test_meeting_persistence_workflow_uses_migrations_and_public_factories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Meeting lifecycle changes persist across independent Unit of Work contexts."""

    database_path = tmp_path / "meeting-workflow.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("AI_MEETING_COPILOT_DATABASE_URL", database_url)
    get_settings.cache_clear()
    container = Container(Settings(database_url=database_url))

    try:
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(_run_meeting_workflow(container))
    finally:
        get_settings.cache_clear()


async def _run_meeting_workflow(container: Container) -> None:
    """Execute and verify the Meeting lifecycle through application factories."""

    await container.start()
    try:
        create_result = await container.get_create_meeting_use_case().execute(
            CreateMeetingCommand(name="Product review")
        )
        meeting_id = create_result.meeting_id

        await container.get_start_meeting_use_case().execute(
            StartMeetingCommand(meeting_id=meeting_id)
        )
        await container.get_rename_meeting_use_case().execute(
            RenameMeetingCommand(
                meeting_id=meeting_id,
                new_name="Customer interview",
            )
        )
        await container.get_end_meeting_use_case().execute(
            EndMeetingCommand(meeting_id=meeting_id)
        )

        async with container.get_unit_of_work() as unit_of_work:
            meeting = await unit_of_work.meetings.get_by_id(meeting_id)

        assert meeting is not None
        assert meeting.id == meeting_id
        assert meeting.name == "Customer interview"
        assert meeting.status is MeetingStatus.ENDED
        assert meeting.started_at is not None
        assert meeting.ended_at is not None
        assert meeting.pull_domain_events() == ()
    finally:
        await container.stop()
