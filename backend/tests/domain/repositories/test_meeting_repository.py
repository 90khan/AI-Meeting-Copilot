"""Tests for the Meeting repository contract shape."""

import inspect

from app.domain.repositories import MeetingRepository


def test_meeting_repository_is_importable() -> None:
    """The public repository contract is available from its package."""

    assert MeetingRepository.__name__ == "MeetingRepository"


def test_meeting_repository_methods_are_async() -> None:
    """The contract declares asynchronous persistence operations."""

    assert inspect.iscoroutinefunction(MeetingRepository.get_by_id)
    assert inspect.iscoroutinefunction(MeetingRepository.save)
    assert inspect.iscoroutinefunction(MeetingRepository.delete)
