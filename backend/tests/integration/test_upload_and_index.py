"""Event and upload API (dev token): validation, quarantine, approval, indexing, dev match.

Also checks that no API response ever contains vectors or scores (invariant 4).
"""

import io
import json
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

FORBIDDEN_KEYS = {"embedding", "embeddings", "vector", "vectors", "prototype", "score", "scores"}


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
    assert all(len(p.stem) == 43 and "f0" not in p.name for p in stored)  # random keys
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


def test_full_flow_index_and_match(api: TestClient, ctx: AppContext, tmp_path: Path) -> None:
    from tests.integration.test_job_queue import drain

    drain(ctx)
    event_id = new_event(api)
    # Person 1 in photos 0-3, person 2 in photos 4-6; each photo also has one stranger.
    photos = [make_png(1, 2, (64 + i, 48)) for i in range(4)] + [
        make_png(2, 2, (80 + i, 48)) for i in range(3)
    ]
    body = upload(api, event_id, photos).json()
    approve = api.post(f"/events/{event_id}/collections/{body['collection_id']}/approve")
    assert approve.status_code == 200 and approve.json()["approved_photos"] == 7
    assert_no_biometrics(approve.json())
    assert (
        api.post(f"/events/{event_id}/collections/{body['collection_id']}/approve").status_code
        == 409
    )
    # Not searchable before the worker ran.
    early = api.post(
        f"/events/{event_id}/match", content=make_png(1, 1), headers={"Content-Type": "image/png"}
    )
    assert early.status_code == 409

    while run_one(ctx, "test-worker"):
        pass
    detail = api.get(f"/events/{event_id}").json()
    assert_no_biometrics(detail)
    assert detail["status"] == "searchable"
    assert detail["counts"]["faces"] == 14 and detail["counts"]["clusters"] >= 2

    # Originals left quarantine; web copies exist and carry no EXIF.
    root = tmp_path / "storage"
    assert not list((root / ctx.settings.storage_bucket_quarantine / event_id).glob("*"))
    assert len(list((root / ctx.settings.storage_bucket_photos / event_id).glob("*"))) == 7
    web = list((root / ctx.settings.storage_bucket_derivatives / event_id).glob("*.jpg"))
    assert len(web) == 7
    with Image.open(io.BytesIO(web[0].read_bytes())) as img:
        assert not img.getexif() and "exif" not in img.info

    ids = body["photo_ids"]
    match = api.post(
        f"/events/{event_id}/match", content=make_png(1, 1), headers={"Content-Type": "image/png"}
    )
    assert match.status_code == 200
    result = match.json()
    assert_no_biometrics(result)
    assert set(result) == {"event_id", "release", "borderline"}
    assert set(result["release"]) <= set(ids[:4]) and result["release"]
    assert not set(result["release"]) & set(ids[4:])

    two_faces = api.post(
        f"/events/{event_id}/match", content=make_png(1, 2), headers={"Content-Type": "image/png"}
    )
    assert two_faces.status_code == 422 and "found 2" in two_faces.json()["detail"]
    no_face = api.post(
        f"/events/{event_id}/match", content=make_png(1, 0), headers={"Content-Type": "image/png"}
    )
    assert no_face.status_code == 422 and "found 0" in no_face.json()["detail"]
    wrong_type = api.post(
        f"/events/{event_id}/match", content=b"x", headers={"Content-Type": "text/plain"}
    )
    assert wrong_type.status_code == 422

    listing = api.get("/events").json()
    assert_no_biometrics(listing)
    assert event_id in {e["event_id"] for e in listing["events"]}

    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        actions = [r[0] for r in s.execute(text("SELECT action FROM audit_log ORDER BY id"))]
        details = json.dumps([r[0] for r in s.execute(text("SELECT details FROM audit_log"))])
    assert actions[:4] == [
        "event.created",
        "photos.uploaded",
        "collection.approved",
        "event.indexed",
    ]
    assert "match.dev" in actions
    assert "." not in details.replace("event.", "")  # counts only, no floats or file names


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


@pytest.mark.parametrize("path", ["/events", "/events/00000000-0000-0000-0000-000000000000"])
def test_dev_routes_need_the_token(api: TestClient, path: str) -> None:
    anonymous = TestClient(api.app)
    assert anonymous.get(path).status_code == 401
    wrong = TestClient(api.app, headers={"Authorization": "Bearer " + "x" * 40})
    assert wrong.get(path).status_code == 401
