"""FastAPI app factory for the FaceScan API process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.adapters.storage.local_filesystem_download import (
    build_local_storage,
    create_download_router,
)
from app.core.config import Settings, get_settings
from app.core.context import AppContext, build_context
from app.core.deps import same_origin_guard
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.multipart import clean_orphaned_spool_files
from app.core.rate_limit import RateLimitStore


class HealthResponse(BaseModel):
    """Liveness probe payload. Contains no configuration or secrets."""

    status: str
    version: str


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    """
    Purpose: build the FastAPI application.
    Inputs: optional Settings (tests inject their own); optional AppContext (tests inject one
        with a test schema, a fake face analyzer and a captured email sender).
    Outputs: a configured FastAPI instance.
    Side effects: configures root logging (with the redaction filter); builds the application
        context when FACESCAN_DB_URL is set; mounts the signed storage download route when
        FACESCAN_STORAGE_SIGNING_KEY is set; adds the same-origin guard for state changes.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    is_prod = settings.env == "prod"
    if ctx is None and settings.db_url is not None:
        ctx = build_context(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Purpose: cleanup orphaned upload spool files on startup and release DB on shutdown."""
        clean_orphaned_spool_files(
            settings.upload_spool_dir,
            max_age_seconds=settings.upload_spool_max_age_seconds,
        )
        yield
        if ctx is not None:
            ctx.engine.dispose()

    app = FastAPI(
        title="FaceScan API",
        version=__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.ctx = ctx
    app.state.rate_limits = RateLimitStore()
    register_error_handlers(app)
    app.middleware("http")(same_origin_guard(settings.auth_rp_origin))

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        """Purpose: liveness probe. Inputs: none. Outputs: status + version. Side effects: none."""
        return HealthResponse(status="ok", version=__version__)

    # Signed downloads for the local filesystem storage adapter (invariant 6).
    if settings.storage_signing_key is not None:
        app.include_router(create_download_router(build_local_storage(settings)))

    from app.modules.consent.router import router as consent_router
    from app.modules.events.router import router as events_router
    from app.modules.gallery.router import router as gallery_router
    from app.modules.identity.router import router as identity_router
    from app.modules.ingest.router import router as ingest_router
    from app.modules.matching.router import router as matching_router

    for router in (
        identity_router,
        events_router,
        ingest_router,
        consent_router,
        matching_router,
        gallery_router,
    ):
        app.include_router(router)
    return app
