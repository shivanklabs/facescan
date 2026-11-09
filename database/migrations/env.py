"""Alembic environment: backend metadata, FACESCAN_DB_URL, optional injected connection.

Tests pass their own connection (config.attributes["connection"]) whose search_path points at
a throwaway schema, so migrations run there instead of in the dev database's public schema.
"""

from alembic import context
from app.core.config import get_settings
from app.db.models import Base
from sqlalchemy import Connection, engine_from_config, pool

target_metadata = Base.metadata


def _url() -> str:
    """Purpose: database URL from settings (never from alembic.ini)."""
    db_url = get_settings().db_url
    if db_url is None:
        raise RuntimeError("FACESCAN_DB_URL is not set")
    return db_url.get_secret_value()


def _run(connection: Connection) -> None:
    """Purpose: run migrations on one connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Purpose: emit SQL without a database connection."""
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Purpose: migrate a live database (injected connection first, else FACESCAN_DB_URL)."""
    injected = context.config.attributes.get("connection")
    if isinstance(injected, Connection):
        _run(injected)
        return
    engine = engine_from_config(
        {"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
