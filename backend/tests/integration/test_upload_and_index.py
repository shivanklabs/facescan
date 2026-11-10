"""Event and upload API (organiser and photographer, passkey sessions): validation, quarantine,
approval and indexing.

Also checks that no API response ever contains vectors or scores (invariant 4).
"""

import io
import uuid
from pathlib import Path

import pytest
from app.core.context import AppContext
from app.db.event_scope import event_transaction
from app.worker import run_one
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from tests.conftest import make_png

FORBIDDEN_KEYS = {
    "embedding",
    "embeddings",
    "vector",
    "vectors",
    "prototype",
    "score",
    "scores",
    "borderline",
}


@pytest.fixture
def api(organiser: TestClient) -> TestClient:
    """The signed-in organiser (organisers may also upload)."""
    return organiser


def assert_no_biometrics(payload: object) -> None:
    """No vector-like arrays and no biometric/score keys anywhere in a JSON response."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key.lower() not in FORBIDDEN_KEYS, key
            assert_no_biometrics(value)
    elif isinstance(payload, list):
        assert not (len(payload) >= 8 and all(isinstance(v, float) for v in payload))
        for value in payload:
            assert_no_biometrics(value)


def new_event(api: TestClient) -> str:
    response = api.post("/events", json={"name": "Wedding", "country": "in"})
    assert response.status_code == 201
    assert_no_biometrics(response.json())
    assert response.json()["country"] == "IN"
    return response.json()["event_id"]


def upload(api: TestClient, event_id: str, files: list[bytes], **form: str) -> object:
    parts = [("files", (f"f{i}.png", data, "image/png")) for i, data in enumerate(files)]
    return api.post(f"/events/{event_id}/photos", files=parts, data=form)


def test_upload_stores_valid_photos_in_quarantine_with_random_keys(
    api: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    event_id = new_event(api)
    response = upload(api, event_id, [make_png(1, 1), make_png(2, 1)])
    assert response.status_code == 201
    body = response.json()
    assert_no_biometrics(body)
    assert len(body["photo_ids"]) == 2
    stored = list(
        (tmp_path / "storage" / ctx.settings.storage_bucket_quarantine / event_id).iterdir()
    )
    assert len(stored) == 2
    assert all(len(p.stem) == 43 and not p.name.startswith("f") for p in stored)  # random keys
    # Adding to the same collection works until it is approved.
    more = upload(api, event_id, [make_png(3, 1)], collection_id=body["collection_id"])
    assert more.status_code == 201 and more.json()["collection_id"] == body["collection_id"]


def test_upload_rejects_by_content_size_pixels_and_count(api: TestClient, ctx: AppContext) -> None:
    event_id = new_event(api)
    text_file = b"<html>not an image</html>"
    r = api.post(
        f"/events/{event_id}/photos", files=[("files", ("x.jpg", text_file, "image/jpeg"))]
    )
    assert r.status_code == 422 and "file 1" in r.json()["detail"]
    fake_png = b"\x89PNG\r\n\x1a\n" + b"garbage" * 20  # right magic, broken content
    assert upload(api, event_id, [fake_png]).status_code == 422
    ctx.settings.upload_max_pixels = 100  # 64x48 is 3072 pixels
    r = upload(api, event_id, [make_png(1, 1)])
    assert r.status_code == 422 and "pixels" in r.json()["detail"]
    ctx.settings.upload_max_pixels = 60_000_000
    ctx.settings.upload_max_bytes = 100
    assert upload(api, event_id, [make_png(1, 1)]).status_code == 413
    ctx.settings.upload_max_bytes = 25 * 1024 * 1024
    ctx.settings.upload_max_files = 2
    assert upload(api, event_id, [make_png(1, 1)] * 3).status_code == 413
    ctx.settings.upload_max_files = 50
    assert upload(api, event_id, []).status_code == 422
    assert upload(api, str(uuid.uuid4()), [make_png(1, 1)]).status_code == 404
    # Nothing was stored by the rejected uploads.
    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        assert s.execute(text("SELECT count(*) FROM photos")).scalar() == 0


def test_approve_indexes_and_makes_web_copies(
    api: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    from tests.integration.test_job_queue import drain

    drain(ctx)
    event_id = new_event(api)
    photos = [make_png(1, 2, (64 + i, 48)) for i in range(4)] + [
        make_png(2, 2, (80 + i, 48)) for i in range(3)
    ]
    body = upload(api, event_id, photos).json()
    approve = api.post(f"/events/{event_id}/collections/{body['collection_id']}/approve")
    assert approve.status_code == 200 and approve.json()["approved_photos"] == 7
    assert_no_biometrics(approve.json())
    again = api.post(f"/events/{event_id}/collections/{body['collection_id']}/approve")
    assert again.status_code == 409
    while run_one(ctx, "test-worker"):
        pass
    detail = api.get(f"/events/{event_id}").json()
    assert_no_biometrics(detail)
    assert detail["status"] == "searchable"
    assert detail["counts"]["faces"] == 14 and detail["counts"]["clusters"] >= 2
    root = tmp_path / "storage"
    assert not list((root / ctx.settings.storage_bucket_quarantine / event_id).glob("*"))
    assert len(list((root / ctx.settings.storage_bucket_photos / event_id).glob("*"))) == 7
    web = list((root / ctx.settings.storage_bucket_derivatives / event_id).glob("*.jpg"))
    assert len(web) == 7
    with Image.open(io.BytesIO(web[0].read_bytes())) as img:
        assert not img.getexif() and "exif" not in img.info
    collections = api.get(f"/events/{event_id}/collections").json()["collections"]
    assert collections[0]["photos"] == 7 and len(collections[0]["previews"]) == 7
    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        actions = [r[0] for r in s.execute(text("SELECT action FROM audit_log ORDER BY id"))]
    assert actions[:4] == [
        "event.created",
        "photos.uploaded",
        "collection.approved",
        "event.indexed",
    ]


def test_openapi_schema_exposes_no_vector_fields(api: TestClient) -> None:
    schema = api.get("/openapi.json").json()
    names: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    names.update(k.lower() for k in value)
                if key == "parameters" and isinstance(value, list):
                    names.update(str(p.get("name", "")).lower() for p in value)
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)
    assert names, "schema has no fields?"
    assert not names & FORBIDDEN_KEYS


def test_staff_routes_need_sign_in_and_a_passkey(api: TestClient, ctx: AppContext) -> None:
    from tests.helpers import magic_sign_in, new_client

    event_id = new_event(api)
    anonymous = new_client(ctx)
    assert anonymous.get("/events").status_code == 401
    assert anonymous.post("/events", json={"name": "x", "country": "IN"}).status_code == 401
    magic_only = new_client(ctx)
    magic_sign_in(magic_only, ctx, "organiser@example.test")
    assert magic_only.post("/events", json={"name": "x", "country": "IN"}).status_code == 403
    assert upload(magic_only, event_id, [make_png(1, 1)]).status_code == 403


def big_png() -> bytes:
    """A valid PNG over 1 MB (random pixels do not compress)."""
    import numpy as np

    pixels = np.random.default_rng(5).integers(0, 256, size=(700, 700, 3), dtype=np.uint8)
    pixels[0, 0] = (1, 1, 0)  # FakeFaceAnalyzer marker: person 1, one face
    buf = io.BytesIO()
    Image.fromarray(pixels).save(buf, format="PNG")
    assert len(buf.getvalue()) > 1024 * 1024
    return buf.getvalue()


def test_large_upload_parts_spool_only_under_the_spool_dir_and_are_deleted(
    api: TestClient, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: verify large multipart upload parts spool only under upload_spool_dir and are
        deleted when the request completes, cross-platform on Windows and Linux, without inspecting
        private SpooledTemporaryFile internals.
    Inputs: api (TestClient), ctx (AppContext), tmp_path (Path), monkeypatch (pytest.MonkeyPatch).
    Outputs: None.
    Side effects: performs multipart upload; intercepts SpooledTemporaryFile creation.
    """
    import app.core.multipart

    recorded_dirs: list[str | None] = []
    real_spooled = app.core.multipart.SpooledTemporaryFile

    def recording_spooled(*args: object, **kwargs: object) -> object:
        recorded_dirs.append(kwargs.get("dir"))  # type: ignore[arg-type]
        return real_spooled(*args, **kwargs)

    monkeypatch.setattr(app.core.multipart, "SpooledTemporaryFile", recording_spooled)
    event_id = new_event(api)
    response = upload(api, event_id, [big_png()])
    assert response.status_code == 201, response.text

    spool_dir = Path(ctx.settings.upload_spool_dir)
    assert recorded_dirs, "a spool file should have been created for the upload part"
    for d in recorded_dirs:
        assert d is not None
        assert Path(d).resolve() == spool_dir.resolve()
    assert list(spool_dir.iterdir()) == []


def test_cluster_membership_is_not_stored_in_plaintext(
    api: TestClient, ctx: AppContext, db_schema: str
) -> None:
    from tests.integration.test_job_queue import drain

    drain(ctx)
    event_id = new_event(api)
    body = upload(api, event_id, [make_png(1, 2, (64 + i, 48)) for i in range(3)]).json()
    api.post(f"/events/{event_id}/collections/{body['collection_id']}/approve")
    while run_one(ctx, "test-worker"):
        pass
    with ctx.engine.connect() as conn:
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name IN ('faces', 'clusters')"
                ),
                {"s": db_schema},
            )
        }
    assert not columns & {"cluster_id", "attached_weak", "prototype_encrypted"}
    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        face_ids = [str(r[0]) for r in s.execute(text("SELECT id FROM faces"))]
        payloads = [bytes(r[0]) for r in s.execute(text("SELECT payload_encrypted FROM clusters"))]
    assert face_ids and payloads
    for payload in payloads:
        assert payload.startswith(b"FSD1")
        for face_id in face_ids:
            assert face_id.encode() not in payload
