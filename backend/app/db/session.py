"""Engine and session factory built from FACESCAN_DB_URL."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings


class DatabaseNotConfiguredError(RuntimeError):
    """FACESCAN_DB_URL is not set."""


def make_engine(settings: Settings, search_path: str | None = None) -> Engine:
    """
    Purpose: SQLAlchemy engine for the facescan role.
    Inputs: settings (db_url required); optional schema search path (tests use their own schema).
    Outputs: Engine with connection health checks. Side effects: none until first use.
    """
    if settings.db_url is None:
        raise DatabaseNotConfiguredError("FACESCAN_DB_URL is not set")
    connect_args: dict[str, str] = {}
    if search_path is not None:
        connect_args["options"] = f"-c search_path={search_path}"
    return create_engine(
        settings.db_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args=connect_args,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Purpose: session factory (no autoflush surprises, objects usable after commit)."""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
