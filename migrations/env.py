from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import create_engine, pool

from recallforge.config import get_config
from recallforge.storage.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Read DB URL from RecallForge settings, bypassing configparser entirely
# to avoid % interpolation issues with URL-encoded passwords.
_db_url = get_config().database_url

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# TODO(M3): Add startup validation that EmbeddingProvider.dim matches the
# vector column dimension in the DDL (e.g. embedding_text_embedding_v4_1024
# should be VECTOR(1024)). Mismatch must fail fast. See M1-design.md and ADR-0001.


def render_item(type_, obj, autogen_context):
    """Register pgvector Vector type so Alembic can render it in migrations."""
    if type_ == "type" and isinstance(obj, Vector):
        return "Vector(%d)" % obj.dim, False
    return None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(_db_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
