"""Tests for centralized logging stream selection."""

import io
import logging
import sys

from app.core.config import Settings
from app.core.logging import get_logger, setup_logging


def test_sidecar_logging_can_reserve_stdout_for_readiness() -> None:
    """Explicit sidecar logging emits operational records only to its stderr stream."""

    root_logger = logging.getLogger()
    previous_streams = [
        handler.stream
        for handler in root_logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and handler.get_name() == "ai_meeting_copilot_console"
    ]
    stderr = io.StringIO()
    try:
        setup_logging(Settings(), stream=stderr)
        get_logger("app.tests.sidecar").warning("operational message")

        assert "operational message" in stderr.getvalue()
        assert sys.stdout is not stderr
    finally:
        setup_logging(
            Settings(),
            stream=previous_streams[0] if previous_streams else sys.stderr,
        )
