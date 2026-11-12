"""Typed application settings loaded from FACESCAN_* environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from the environment (or a git-ignored .env). No secret has a default."""

    model_config = SettingsConfigDict(env_prefix="FACESCAN_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod", "public"] = "dev"
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
    backup_dir: str | None = None

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
    # Event-photo upload parts over 1 MB spool here while a request is parsed (deleted when it
    # ends). Must be under var/ (git-ignored). Selfies never spool.
    upload_spool_dir: str = "var/tmp/uploads"
    upload_spool_max_age_seconds: int = Field(default=3600, ge=60)

    # Sign-in: email magic links + passkeys (WebAuthn); server-side sessions in PostgreSQL.
    auth_rp_id: str = "localhost"
    auth_rp_name: str = "FaceScan"
    auth_rp_origin: str = "http://localhost:5173"  # the web app's origin (also the CSRF check)
    auth_session_hours: int = Field(default=12, ge=1, le=24 * 30)
    auth_magic_link_minutes: int = Field(default=15, ge=1, le=60)
    auth_magic_links_per_hour: int = Field(default=5, ge=1, le=50)
    auth_cookie_secure: bool = True
    # Who may create events (comma-separated emails). Organisers must use a passkey.
    auth_organiser_emails: str = ""
    # Who may view the platform admin health page (comma-separated emails).
    # Admins must use a passkey.
    auth_admin_emails: str = ""
    # Google sign-in (milestone 7): the OAuth web client id; empty disables Google sign-in.
    google_client_id: str = Field(default="", max_length=200)
    google_timeout_seconds: float = Field(default=5.0, gt=0, le=15)
    # Magic-link delivery: "console" (dev only) or "smtp_gmail" (production / public testing).
    email_adapter: Literal["console", "smtp_gmail"] = "console"
    email_smtp_user: str | None = None
    email_smtp_password: SecretStr | None = None
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = Field(default=587, ge=1, le=65535)
    email_smtp_use_tls: bool = True

    # Gemini scene query rewriting (optional; falls back to local query on failure/missing key).
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: float = Field(default=2.5, gt=0, le=10)

    # Scene search and claims (B & C).
    scene_search_per_guest_per_day: int = Field(default=10, ge=1, le=1000)
    claims_per_guest_per_day: int = Field(default=5, ge=1, le=500)
    preview_max_side: int = Field(default=480, ge=256, le=1024)
    preview_jpeg_quality: int = Field(default=85, ge=50, le=100)
    attempt_retention_days: int = Field(default=7, ge=1, le=90)

    # Event access and guest search.
    invite_default_days: int = Field(default=14, ge=1, le=90)
    search_per_user_per_hour: int = Field(default=10, ge=1, le=1000)
    search_per_event_per_hour: int = Field(default=500, ge=1, le=100_000)
    search_min_frames: int = Field(default=3, ge=1, le=10)
    search_max_frames: int = Field(default=5, ge=1, le=10)
    # Simple-site scan (milestone 7): front frame, a burst while blinking, front frame.
    discover_max_frames: int = Field(default=7, ge=3, le=10)
    # Liveness adapter: silent_face (default) or empty (dev only).
    liveness_adapter: Literal["", "silent_face"] = "silent_face"
    search_front_only_threshold_offset: float = Field(default=0.03, ge=0.0, le=0.2)
    consent_notice_path: str = "config/consent/face_search_notice.yaml"

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
