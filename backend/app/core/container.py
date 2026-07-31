"""Application composition root."""

import logging

from app.core.config import Settings
from app.core.logging import get_logger, setup_logging


class Container:
    """Explicitly wire application dependencies and manage their lifecycle."""

    def __init__(self, settings: Settings) -> None:
        """Create a container using the supplied immutable application settings."""

        self._settings = settings
        setup_logging(settings)

    async def start(self) -> None:
        """Start managed infrastructure resources when they are introduced."""

    async def stop(self) -> None:
        """Stop managed infrastructure resources when they are introduced."""

    def get_settings(self) -> Settings:
        """Return the container's immutable application settings."""

        return self._settings

    def get_logger(self, name: str) -> logging.Logger:
        """Return a module-qualified logger configured by this container."""

        return get_logger(name)
