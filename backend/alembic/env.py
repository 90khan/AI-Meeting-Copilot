"""Alembic migration environment configured from application settings."""

from logging.config import fileConfig

from alembic import context
from app.core.config import get_settings
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    TranscriptEntryModel,
)
from app.infrastructure.persistence.sqlalchemy.models.meeting_translation_artifact_model import (
    MeetingTranslationArtifactModel,
)
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
ORM_MODELS = (MeetingModel, TranscriptEntryModel, MeetingTranslationArtifactModel)


def _database_url() -> str:
    """Return the database URL from validated application settings."""

    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
