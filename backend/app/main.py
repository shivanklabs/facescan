"""FastAPI app factory for the FaceScan API process."""

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging


class HealthResponse(BaseModel):
    """Liveness probe payload. Contains no configuration or secrets."""

    status: str
    version: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Purpose: build the FastAPI application.
    Inputs: optional Settings (tests inject their own); defaults to environment settings.
    Outputs: a configured FastAPI instance.
    Side effects: configures root logging (with the redaction filter).
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    is_prod = settings.env == "prod"
    app = FastAPI(
        title="FaceScan API",
        version=__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    register_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        """Purpose: liveness probe. Inputs: none. Outputs: status + version. Side effects: none."""
        return HealthResponse(status="ok", version=__version__)

    # TODO: include module routers (identity, events, ingest, gallery, consent, admin, ...).
    return app
