"""Shared async test configuration for application use-case tests."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run application use-case tests on the project's asyncio runtime."""

    return "asyncio"
