"""SQLAlchemy engine factory."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.core.config import Settings


def create_engine_from_settings(settings: Settings) -> Engine:
    """Create an Engine configured from application settings."""

    return create_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
        echo=settings.debug,
    )
