"""Shared fixtures.

Database tests use the local PostgreSQL from the repository's .env (FACESCAN_DB_URL, the
facescan role). Each test module gets its own throwaway schema, migrated with the real Alembic
migration and dropped afterwards. Without a reachable database those tests are skipped.

FakeFaceAnalyzer needs no models: an image's top-left pixel encodes a person (red channel) and
the number of faces (green channel), so ingest, indexing and matching run end to end while
clustering, calibration and matching use the real facescan_ai code.
"""

import base64
import io
import secrets
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from app.adapters.vision.facescan_ai_face_analyzer import FacescanAiFaceAnalyzer
from app.core.config import Settings
from app.core.context import AppContext, build_context
from app.main import create_app
from app.ports.face_analyzer import AnalysedFace, SelfieQuery, SelfieRejectedError
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

REPO = Path(__file__).resolve().parents[2]
DIMS = {"auraface": 512, "sface": 128}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, env="test", worker_poll_interval_seconds=0.01)


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
        for k in range(count):
            who = person if k == 0 else 200 + k  # extra faces are strangers
            seed = int.from_bytes(data[-8:], "little") + k
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


# ---------------------------------------------------------------- database


def _repo_settings(**overrides: object) -> Settings:
    return Settings(_env_file=REPO / ".env", **overrides)


@pytest.fixture(scope="module")
def db_schema() -> Iterator[str]:
    """A migrated throwaway schema in the dev database (skips without a database)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    base = _repo_settings()
    if base.db_url is None:
        pytest.skip("FACESCAN_DB_URL is not set (database tests skipped)")
    schema = "test_" + secrets.token_hex(6)
    admin = create_engine(base.db_url.get_secret_value())
    try:
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    except OperationalError:
        admin.dispose()
        pytest.skip("PostgreSQL is not reachable (database tests skipped)")
    migrator = create_engine(
        base.db_url.get_secret_value(), connect_args={"options": f"-c search_path={schema}"}
    )
    try:
        with migrator.begin() as conn:
            cfg = Config(str(REPO / "database" / "alembic.ini"))
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        yield schema
    finally:
        migrator.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


DEV_TOKEN = "t" * 40


@pytest.fixture
def ctx(db_schema: str, tmp_path: Path) -> Iterator[AppContext]:
    """Application context on the test schema with local storage under tmp_path."""
    settings = _repo_settings(
        env="dev",
        dev_admin_token=SecretStr(DEV_TOKEN),
        storage_root=str(tmp_path / "storage"),
        upload_spool_dir=str(tmp_path / "spool"),
        storage_signing_key=SecretStr("s" * 48),
        keys_master_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
        job_retry_base_seconds=0.01,
        worker_poll_interval_seconds=0.01,
    )
    context = build_context(settings, analyzer=FakeFaceAnalyzer(), search_path=db_schema)
    yield context
    context.engine.dispose()


@pytest.fixture
def api(ctx: AppContext) -> TestClient:
    """API client with the dev token header."""
    test_client = TestClient(create_app(ctx.settings, ctx=ctx))
    test_client.headers["Authorization"] = f"Bearer {DEV_TOKEN}"
    return test_client
