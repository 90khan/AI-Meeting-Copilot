"""Tests for the SQLAlchemy database foundation."""

from app.core.config import Settings
from app.infrastructure.database.engine import create_engine_from_settings
from app.infrastructure.database.session import create_session_factory
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


def test_settings_uses_the_default_database_url() -> None:
    """Database configuration has the expected local SQLite default."""

    assert Settings().database_url == "sqlite+pysqlite:///./data/app.db"


def test_create_engine_from_settings_returns_an_engine() -> None:
    """The engine factory creates an Engine without opening a session."""

    engine = create_engine_from_settings(
        Settings(database_url="sqlite+pysqlite:///:memory:")
    )

    try:
        assert isinstance(engine, Engine)
        assert engine.url.drivername == "sqlite+pysqlite"
    finally:
        engine.dispose()


def test_create_session_factory_returns_a_bound_sessionmaker() -> None:
    """The session factory is bound to the supplied Engine."""

    engine = create_engine_from_settings(
        Settings(database_url="sqlite+pysqlite:///:memory:")
    )

    try:
        session_factory = create_session_factory(engine)

        assert isinstance(session_factory, sessionmaker)
        assert session_factory.kw["bind"] is engine
        assert session_factory.kw["autoflush"] is False
        assert session_factory.kw["expire_on_commit"] is False
    finally:
        engine.dispose()
