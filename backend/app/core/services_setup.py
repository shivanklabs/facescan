"""Application context: the adapters behind each port, built once per process from settings.

The API (lifespan) and the worker both build one. Tests build their own with a test schema
and may inject a fake face analyzer.
"""

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.encryption_keys.env_master_key import EnvMasterKeyManager, parse_master_key
from app.connectors.job_queue.postgres_job_queue import PostgresJobQueue
from app.connectors.photo_storage.local_filesystem_download import build_local_storage
from app.core.settings import Settings
from app.db.session import make_engine, make_session_factory
from app.interfaces.encryption_keys import KeyManager
from app.interfaces.face_analyzer import FaceAnalyzer
from app.interfaces.job_queue import JobQueue
from app.interfaces.live_face_check import Liveness
from app.interfaces.photo_storage import ObjectStorage
from app.interfaces.scene_search_ai import QueryRewriter


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
    liveness: Liveness | None = None
    rewriter: QueryRewriter | None = None


def build_context(
    settings: Settings,
    analyzer: FaceAnalyzer | None = None,
    search_path: str | None = None,
    liveness: Liveness | None = None,
    rewriter: QueryRewriter | None = None,
) -> AppContext:
    """
    Purpose: build the adapters from settings (services.yaml defaults: local storage,
        env master key, Postgres queue, facescan_ai).
    Inputs: settings; optional analyzer, schema search path, liveness and rewriter (tests).
    Outputs: AppContext. Side effects: creates a DB engine (no connection yet).
    Raises ContextConfigError naming the missing setting.
    """
    if settings.db_url is None:
        raise ContextConfigError("FACESCAN_DB_URL is not set")
    if settings.keys_master_key is None:
        raise ContextConfigError("FACESCAN_KEYS_MASTER_KEY is not set")
    if settings.storage_signing_key is None:
        raise ContextConfigError("FACESCAN_STORAGE_SIGNING_KEY is not set")
    if settings.env in ("prod", "public") and settings.liveness_adapter != "silent_face":
        raise ContextConfigError("the silent_face liveness adapter is required in public/prod mode")
    try:
        master = parse_master_key(settings.keys_master_key.get_secret_value())
    except ValueError as exc:
        raise ContextConfigError(str(exc)) from exc
    engine = make_engine(settings, search_path=search_path)
    sessions = make_session_factory(engine)
    if analyzer is None:
        from app.connectors.face_ai.face_analyzer import FacescanAiFaceAnalyzer

        analyzer = FacescanAiFaceAnalyzer()
    if liveness is None and settings.liveness_adapter == "silent_face":
        from app.connectors.live_face_check.silent_face_liveness import SilentFaceLiveness

        liveness = SilentFaceLiveness()
    if rewriter is None:
        from app.connectors.scene_search_ai.gemini_query_rewriter import GeminiQueryRewriter

        rewriter = GeminiQueryRewriter(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
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
        liveness=liveness,
        rewriter=rewriter,
    )


def guest_search_allowed(settings: Settings) -> bool:
    """
    Purpose: guest face search runs in dev or when an active liveness adapter (silent_face)
        is configured. Note: this mitigates presentation attacks (screens, paper prints);
        it does not stop injection attacks (direct synthetic API frame submission).
    Inputs: Settings. Outputs: bool. Side effects: none.
    """
    return settings.env == "dev" or settings.liveness_adapter == "silent_face"
