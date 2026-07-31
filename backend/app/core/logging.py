"""Centralized console logging configuration."""

import logging
from typing import TextIO

from app.core.config import Settings

_CONSOLE_HANDLER_NAME = "ai_meeting_copilot_console"
_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)


def setup_logging(settings: Settings) -> None:
    """Configure idempotent console logging from application settings."""

    level = _resolve_log_level(settings.log_level)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    handler = _get_console_handler(root_logger)
    handler.setLevel(level)
    handler.setFormatter(_FORMATTER)


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the requested module-qualified name."""

    return logging.getLogger(name)


def _get_console_handler(root_logger: logging.Logger) -> logging.StreamHandler[TextIO]:
    """Return the application's console handler, creating it when needed."""

    for handler in root_logger.handlers:
        if handler.get_name() == _CONSOLE_HANDLER_NAME and isinstance(
            handler, logging.StreamHandler
        ):
            return handler

    handler = logging.StreamHandler()
    handler.set_name(_CONSOLE_HANDLER_NAME)
    root_logger.addHandler(handler)
    return handler


def _resolve_log_level(level_name: str) -> int:
    """Convert a configured level name to its standard logging value."""

    level = logging.getLevelNamesMapping().get(level_name.upper())
    if level is None:
        message = f"Unsupported log level: {level_name}"
        raise ValueError(message)
    return level
