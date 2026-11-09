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
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.security import check_dev_token_allowed, dev_token_enabled


class HealthResponse(BaseModel):
    """Liveness probe payload. Contains no configuration or secrets."""

    status: str
    version: str


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    """
    Purpose: build the FastAPI application.
    Inputs: optional Settings (tests inject their own); optional AppContext (tests inject one
        with a test schema and a fake face analyzer); defaults come from the environment.
    Outputs: a configured FastAPI instance.
    Side effects: configures root logging (with the redaction filter); mounts the signed
        storage download route when FACESCAN_STORAGE_SIGNING_KEY is set; mounts the TEMPORARY
        dev-only event/upload/match routes when FACESCAN_DEV_ADMIN_TOKEN is set in dev.
    Raises DevTokenNotAllowedError (refuses to start) if the dev token is set outside dev.
    """
    settings = settings or get_settings()
    check_dev_token_allowed(settings)
    configure_logging(settings.log_level)
    is_prod = settings.env == "prod"
    dev_routes = dev_token_enabled(settings)
    if dev_routes and ctx is None:
        ctx = build_context(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Purpose: release database connections on shutdown."""
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
    register_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        """Purpose: liveness probe. Inputs: none. Outputs: status + version. Side effects: none."""
        return HealthResponse(status="ok", version=__version__)

    # Signed downloads for the local filesystem storage adapter (invariant 6).
    if settings.storage_signing_key is not None:
        app.include_router(create_download_router(build_local_storage(settings)))

    # TEMPORARY DEV-ONLY routes (dev admin token) until login is built.
    if dev_routes:
        from app.modules.events.router import router as events_router
        from app.modules.ingest.router import router as ingest_router
        from app.modules.matching.router import router as matching_router

        app.include_router(events_router)
        app.include_router(ingest_router)
        app.include_router(matching_router)

    # TODO: include module routers (identity, gallery, consent, admin, ...) with login.
    return app
