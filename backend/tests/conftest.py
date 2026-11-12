"""Shared fixtures.

Database tests use the PostgreSQL URL provided via FACESCAN_TEST_DB_URL or FACESCAN_DB_URL
(the facescan role). Each test module gets its own throwaway schema, migrated with the real
Alembic migration and dropped afterwards. Without a reachable database those tests fail
unless --skip-db is specified.

FakeFaceAnalyzer needs no models: an image's top-left pixel encodes a person (red channel) and
the number of faces (green channel), so ingest, indexing and matching run end to end while
clustering, calibration and matching use the real facescan_ai code.
"""

import base64
import io
import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from app.connectors.vision.facescan_ai_face_analyzer import FacescanAiFaceAnalyzer
from app.core.config import Settings
from app.core.context import AppContext, build_context
from app.interfaces.face_analyzer import AnalysedFace, SelfieQuery, SelfieRejectedError
from app.main import create_app
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

REPO = Path(__file__).resolve().parents[2]
DIMS = {"auraface": 512, "sface": 128}


def pytest_addoption(parser: pytest.Parser) -> None:
    """
    Purpose: register --skip-db CLI flag allowing explicit skipping of database/security tests.
    Inputs: parser (pytest.Parser).
    Outputs: None.
    Side effects: registers pytest command-line option.
    """
    parser.addoption(
        "--skip-db",
        action="store_true",
        default=False,
        help="explicitly skip database and security tests that require PostgreSQL",
    )


@pytest.fixture
def settings() -> Settings:
    """
    Purpose: produce application settings for unit tests with env-file loading disabled,
        db_url set to None, and a freshly generated random master key.
    Inputs: none.
    Outputs: Settings instance.
    Side effects: none.
    """
    master_key = base64.b64encode(secrets.token_bytes(32)).decode()
    return Settings(
        _env_file=None,
        env="test",
        db_url=None,
        storage_signing_key=None,
        keys_master_key=SecretStr(master_key),
        worker_poll_interval_seconds=0.01,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# ---------------------------------------------------------------- synthetic images and faces


def make_png(person: int, faces: int, size: tuple[int, int] = (64, 48)) -> bytes:
    """A small PNG whose top-left pixel encodes (person, face count)."""
    img = Image.new("RGB", size, (200, 200, 200))
    img.putpixel((0, 0), (person, faces, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def person_vector(person: int, engine: str, noise_seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(person * 1000 + DIMS[engine])
    base = rng.normal(size=DIMS[engine])
    noise = np.random.default_rng(noise_seed).normal(size=DIMS[engine]) * 0.15
    v = base + noise
    return (v / np.linalg.norm(v)).astype(np.float32)


def _decode_marker(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        r, g, *_ = img.convert("RGB").getpixel((0, 0))
    return int(r), int(g)


class FakeFaceAnalyzer(FacescanAiFaceAnalyzer):
    """FaceAnalyzer without models (see module docstring). Real inspect/web_copy/build/match."""

    @property
    def engine_versions(self) -> dict[str, str]:
        return {"auraface": "fake", "sface": "fake"}

    def analyse_photo(self, data: bytes) -> list[AnalysedFace]:
        person, count = _decode_marker(data)
        faces = []
        # Standard seed for synthetic faces across PNG and JPEG container formats
        canon_seed = int.from_bytes(b"IEND\xaeB\x60\x82", "little")
        for k in range(count):
            who = person if k == 0 else 200 + k  # extra faces are strangers
            seed = (
                canon_seed + k
                if data[-2:] == b"\xff\xd9"
                else int.from_bytes(data[-8:], "little") + k
            )
            faces.append(
                AnalysedFace(
                    bbox=(10.0 + 120 * k, 10.0, 100.0, 120.0),
                    landmarks=((0.0, 0.0),) * 5,
                    detector_score=0.95,
                    quality=0.9,
                    embeddings={e: person_vector(who, e, seed) for e in DIMS},
                )
            )
        return faces

    def selfie_query(self, data: bytes) -> SelfieQuery:
        person, count = _decode_marker(data)
        if count != 1:
            raise SelfieRejectedError(count)
        return SelfieQuery(vectors={e: person_vector(person, e, 99) for e in DIMS}, strong=True)

    def embed_scene(self, data: bytes) -> np.ndarray:
        """
        Purpose: deterministic 768-dim normalized vector for tests without models.
        Inputs: data (bytes). Outputs: np.ndarray shape (768,). Side effects: none.
        """
        seed = int.from_bytes(data[-8:], "little") if len(data) >= 8 else len(data)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(768).astype(np.float32)
        return (v / np.linalg.norm(v)).astype(np.float32)

    def embed_scene_query(self, text: str) -> np.ndarray:
        """
        Purpose: deterministic 768-dim normalized vector for query text without models.
        Inputs: text (str). Outputs: np.ndarray shape (768,). Side effects: none.
        """
        import hashlib

        h = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
        rng = np.random.default_rng(h)
        v = rng.standard_normal(768).astype(np.float32)
        return (v / np.linalg.norm(v)).astype(np.float32)

    def blurred_preview(self, data: bytes, max_side: int = 480, jpeg_quality: int = 85) -> bytes:
        """
        Purpose: blurred JPEG preview for tests without requiring ONNX models.
        Inputs: data (bytes), max_side (int), jpeg_quality (int).
        Outputs: bytes. Side effects: none.
        """
        from PIL import ImageFilter

        with Image.open(io.BytesIO(data)) as img:
            img.thumbnail((max_side, max_side))
            blurred = img.filter(ImageFilter.GaussianBlur(radius=15))
            buf = io.BytesIO()
            blurred.convert("RGB").save(buf, format="JPEG", quality=jpeg_quality)
            return buf.getvalue()


# ---------------------------------------------------------------- database


def _repo_settings(**overrides: object) -> Settings:
    """
    Purpose: produce settings for database/integration tests with env-file loading disabled.
        Explicitly injects FACESCAN_TEST_DB_URL or FACESCAN_DB_URL if db_url is not in overrides.
    Inputs: arbitrary keyword overrides.
    Outputs: Settings instance.
    Side effects: none.
    """
    if "db_url" not in overrides:
        url = os.environ.get("FACESCAN_TEST_DB_URL") or os.environ.get("FACESCAN_DB_URL")
        if url:
            overrides["db_url"] = SecretStr(url)
    return Settings(_env_file=None, **overrides)


_db_reachability_error: str | None = None


@pytest.fixture(scope="module")
def db_schema(request: pytest.FixtureRequest) -> Iterator[str]:
    """
    Purpose: provide a migrated throwaway schema in PostgreSQL for database and security tests.
        Fails with a clear message if PostgreSQL is unavailable, unless --skip-db is
        explicitly passed.
    Inputs: request (pytest.FixtureRequest).
    Outputs: Iterator yielding generated schema name.
    Side effects: creates database schema, runs Alembic migrations, drops schema on teardown.
    """
    global _db_reachability_error
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    skip_db = bool(
        request.config.getoption("--skip-db", default=False)
        or os.environ.get("FACESCAN_SKIP_DB") == "1"
    )

    if _db_reachability_error is not None:
        if skip_db:
            pytest.skip(_db_reachability_error)
        pytest.fail(_db_reachability_error)

    base = _repo_settings()
    if base.db_url is None:
        if skip_db:
            pytest.skip("database tests skipped via --skip-db flag")
        _db_reachability_error = (
            "Database tests require a database URL (set FACESCAN_TEST_DB_URL or FACESCAN_DB_URL, "
            "or pass --skip-db to explicitly skip database tests)."
        )
        pytest.fail(_db_reachability_error)
    schema = "test_" + secrets.token_hex(6)
    admin = create_engine(base.db_url.get_secret_value(), connect_args={"connect_timeout": 5})
    try:
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    except OperationalError as exc:
        admin.dispose()
        _db_reachability_error = (
            f"PostgreSQL is not reachable at configured database URL: {exc}. "
            "Ensure PostgreSQL is running, or pass --skip-db to explicitly skip database tests."
        )
        if skip_db:
            pytest.skip(_db_reachability_error)
        pytest.fail(_db_reachability_error)
    migrator = create_engine(
        base.db_url.get_secret_value(),
        connect_args={"options": f"-c search_path={schema}", "connect_timeout": 5},
    )
    try:
        with migrator.begin() as conn:
            cfg = Config(str(REPO / "database" / "alembic.ini"))
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        yield schema
    except OperationalError as exc:
        _db_reachability_error = (
            f"PostgreSQL migration connection failed: {exc}. "
            "Ensure PostgreSQL is running, or pass --skip-db to explicitly skip database tests."
        )
        if skip_db:
            pytest.skip(_db_reachability_error)
        pytest.fail(_db_reachability_error)
    finally:
        migrator.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.fixture
def ctx(db_schema: str, tmp_path: Path) -> Iterator[AppContext]:
    """
    Purpose: application context on the test schema with local storage under tmp_path,
        captured email, one allow-listed organiser, and freshly generated random keys.
    Inputs: db_schema, tmp_path.
    Outputs: Iterator yielding AppContext.
    Side effects: creates database tables; disposes engine on teardown.
    """
    from tests.helpers import ORGANISER_EMAIL, ORIGIN, MemoryEmailSender

    signing_key = secrets.token_hex(32)
    master_key = base64.b64encode(secrets.token_bytes(32)).decode()
    settings = _repo_settings(
        env="dev",
        auth_rp_id="localhost",
        auth_rp_origin=ORIGIN,
        auth_organiser_emails=ORGANISER_EMAIL,
        storage_root=str(tmp_path / "storage"),
        upload_spool_dir=str(tmp_path / "spool"),
        storage_signing_key=SecretStr(signing_key),
        keys_master_key=SecretStr(master_key),
        job_retry_base_seconds=0.01,
        worker_poll_interval_seconds=0.01,
        liveness_adapter="",
    )
    context = build_context(
        settings, analyzer=FakeFaceAnalyzer(), search_path=db_schema, email=MemoryEmailSender()
    )
    yield context
    context.engine.dispose()


@pytest.fixture
def organiser(ctx: AppContext) -> TestClient:
    """The allow-listed organiser, signed in with a passkey."""
    from tests.helpers import ORGANISER_EMAIL, staff_client

    return staff_client(ctx, ORGANISER_EMAIL)
