"""FastAPI app factory for the FaceScan API process."""

import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from pydantic import BaseModel
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

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


def _get_dist_dir() -> Path:
    """
    Purpose: resolve the frontend dist directory path.
    Inputs: None.
    Outputs: Path to dist folder candidate.
    Side Effects: checks filesystem existence.
    """
    dist_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not dist_dir.exists():
        dist_dir = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    return dist_dir


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    """
    Purpose: build the FastAPI application.
    Inputs: optional Settings (tests inject their own); optional AppContext (tests inject one
        with a test schema, a fake face analyzer and a captured email sender).
    Outputs: a configured FastAPI instance.
    Side effects: configures root logging (with the redaction filter); builds the application
        context when FACESCAN_DB_URL is set; mounts the signed storage download route when
        FACESCAN_STORAGE_SIGNING_KEY is set; adds the same-origin guard for state changes.
        In public/production mode every backend route lives only under /api, so browser
        pages such as /auth/callback reach the SPA fallback; dev also mounts them at the
        root (the Vite proxy strips /api).
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    is_prod = settings.env in ("prod", "production", "public")
    api_mounts = ("/api",) if is_prod else ("", "/api")
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

    def health() -> HealthResponse:
        """Purpose: liveness probe. Inputs: none. Outputs: status + version. Side effects: none."""
        return HealthResponse(status="ok", version=__version__)

    for mount in api_mounts:
        app.get(f"{mount}/health", response_model=HealthResponse, tags=["ops"])(health)

    # Signed downloads for the local filesystem storage adapter (invariant 6).
    if settings.storage_signing_key is not None:
        download_router = create_download_router(build_local_storage(settings))
        for mount in api_mounts:
            app.include_router(download_router, prefix=mount)

    from app.modules.admin.router import router as admin_router
    from app.modules.consent.router import router as consent_router
    from app.modules.events.router import router as events_router
    from app.modules.gallery.router import router as gallery_router
    from app.modules.identity.router import router as identity_router
    from app.modules.ingest.router import router as ingest_router
    from app.modules.matching.router import router as matching_router
    from app.modules.scene_search.router import (
        claims_router,
    )
    from app.modules.scene_search.router import (
        router as scene_search_router,
    )

    for router in (
        identity_router,
        events_router,
        ingest_router,
        consent_router,
        matching_router,
        gallery_router,
        scene_search_router,
        claims_router,
        admin_router,
    ):
        for mount in api_mounts:
            app.include_router(router, prefix=mount)

    # In public or production mode (when frontend/dist is built), serve frontend SPA
    dist_dir = _get_dist_dir()
    if is_prod and dist_dir.exists() and (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="spa_assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> Response:
            """
            Purpose: serve static SPA files or fallback to index.html for client-side routing.
            Inputs: full_path (str) requested path.
            Outputs: FileResponse with file or index.html (JSON 404 only for unmapped /api).
            Side effects: reads static file from filesystem.
            """
            if full_path.split("/", 1)[0] == "api":
                return Response(
                    content='{"detail":"Not Found"}',
                    status_code=404,
                    media_type="application/json",
                )
            dist_resolved = dist_dir.resolve()
            try:
                candidate = (dist_resolved / urllib.parse.unquote(full_path)).resolve()
            except (ValueError, OSError):
                candidate = None
            if (
                candidate is not None
                and candidate.is_relative_to(dist_resolved)
                and candidate.is_file()
            ):
                return FileResponse(str(candidate))
            return FileResponse(str(dist_resolved / "index.html"))

    return app
