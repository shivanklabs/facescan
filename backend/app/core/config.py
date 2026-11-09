"""Typed application settings loaded from FACESCAN_* environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from the environment (or a git-ignored .env). No secret has a default."""

    model_config = SettingsConfigDict(env_prefix="FACESCAN_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    db_url: SecretStr | None = None

    # Local filesystem storage (default adapter). The signing key is required to serve files.
    storage_root: str = "var/storage"
    storage_bucket_photos: str = Field(
        default="facescan-photos", pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
    )
    storage_bucket_derivatives: str = Field(
        default="facescan-derivatives", pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
    )
    # Uploads land here until a collection is approved.
    storage_bucket_quarantine: str = Field(
        default="facescan-quarantine", pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
    )
    storage_signing_key: SecretStr | None = Field(default=None, min_length=32)
    storage_url_expiry_minutes: int = Field(default=5, ge=1, le=60)
    storage_public_base_url: str = "http://localhost:8000"

    # S3-compatible storage (production placeholder, decided in Phase 3).
    storage_endpoint: str | None = None
    storage_access_key: SecretStr | None = None
    storage_secret_key: SecretStr | None = None
    keys_master_key: SecretStr | None = None

    # Uploads and selfies (limits are enforced before anything is stored).
    upload_max_files: int = Field(default=50, ge=1, le=500)
    upload_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    upload_max_pixels: int = Field(default=60_000_000, ge=1)
    selfie_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    web_copy_max_side: int = Field(default=2048, ge=256, le=8192)

    # TEMPORARY DEV-ONLY ACCESS (stand-in until login is built): a single admin bearer token.
    # Accepted only when env == "dev"; the API refuses to start if it is set anywhere else.
    dev_admin_token: SecretStr | None = Field(default=None, min_length=32)

    worker_poll_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_seconds: float = Field(default=10.0, gt=0, le=3600)
    job_lock_timeout_seconds: float = Field(default=1800.0, ge=60)

    config_adapters_path: str = "config/adapters.yaml"
    config_policies_dir: str = "config/policies"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Purpose: cached settings singleton. Outputs: Settings. Side effects: reads env once."""
    return Settings()
