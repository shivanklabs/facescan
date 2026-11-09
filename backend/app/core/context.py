"""Application context: the adapters behind each port, built once per process from settings.

The API (lifespan) and the worker both build one. Tests build their own with a test schema
and may inject a fake face analyzer.
"""

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.keys.env_master_key import EnvMasterKeyManager, parse_master_key
from app.adapters.queue.postgres_job_queue import PostgresJobQueue
from app.adapters.storage.local_filesystem_download import build_local_storage
from app.core.config import Settings
from app.db.session import make_engine, make_session_factory
from app.ports.face_analyzer import FaceAnalyzer
from app.ports.job_queue import JobQueue
from app.ports.key_manager import KeyManager
from app.ports.object_storage import ObjectStorage


class ContextConfigError(RuntimeError):
    """A setting needed by the database-backed features is missing (message names it)."""


@dataclass(slots=True)
class AppContext:
    """Everything services need; no request state."""

    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    storage: ObjectStorage
    keys: KeyManager
    queue: JobQueue
    analyzer: FaceAnalyzer


def build_context(
    settings: Settings,
    analyzer: FaceAnalyzer | None = None,
    search_path: str | None = None,
) -> AppContext:
    """
    Purpose: build the adapters from settings (adapters.yaml defaults: local storage,
        env master key, Postgres queue, facescan_ai).
    Inputs: settings; optional analyzer (tests); optional schema search path (tests).
    Outputs: AppContext. Side effects: creates a DB engine (no connection yet).
    Raises ContextConfigError naming the missing setting.
    """
    if settings.db_url is None:
        raise ContextConfigError("FACESCAN_DB_URL is not set")
    if settings.keys_master_key is None:
        raise ContextConfigError("FACESCAN_KEYS_MASTER_KEY is not set")
    if settings.storage_signing_key is None:
        raise ContextConfigError("FACESCAN_STORAGE_SIGNING_KEY is not set")
    try:
        master = parse_master_key(settings.keys_master_key.get_secret_value())
    except ValueError as exc:
        raise ContextConfigError(str(exc)) from exc
    engine = make_engine(settings, search_path=search_path)
    sessions = make_session_factory(engine)
    if analyzer is None:
        from app.adapters.vision.facescan_ai_face_analyzer import FacescanAiFaceAnalyzer

        analyzer = FacescanAiFaceAnalyzer()
    return AppContext(
        settings=settings,
        engine=engine,
        sessions=sessions,
        storage=build_local_storage(settings),
        keys=EnvMasterKeyManager(master),
        queue=PostgresJobQueue(
            sessions,
            max_attempts=settings.job_max_attempts,
            retry_base_seconds=settings.job_retry_base_seconds,
            lock_timeout_seconds=settings.job_lock_timeout_seconds,
        ),
        analyzer=analyzer,
    )
