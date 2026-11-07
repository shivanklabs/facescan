"""Alembic environment: uses backend metadata and FACESCAN_DB_URL."""

from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from sqlalchemy import engine_from_config, pool

target_metadata = Base.metadata


def _url() -> str:
    db_url = get_settings().db_url
    if db_url is None:
        raise RuntimeError("FACESCAN_DB_URL is not set")
    return db_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(
        {"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
