"""Integration tests for chunked upload concurrency, size limits, and failure handling."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from app.core.context import AppContext
from app.core.errors import UnprocessableError
from app.db.event_scope import event_transaction
from app.features.photo_upload.models import UploadSession
from fastapi.testclient import TestClient
from tests.conftest import make_png
from tests.helpers import searchable_event


def test_chunked_upload_refuses_oversized_byte_size(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that init_chunked_upload refuses byte_size exceeding upload_max_bytes with 413.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP 413 response.
    """
    event_id, _, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    limit = ctx.settings.upload_max_bytes
    res = organiser.post(
        f"/events/{event_id}/uploads/init",
        json={
            "filename": "huge.jpg",
            "byte_size": limit + 1,
            "total_chunks": 5,
            "expected_sha256": "0" * 64,
        },
    )
    assert res.status_code == 413
    assert "upload limit" in res.json()["detail"]


def test_chunked_upload_concurrent_chunks_with_for_update(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that concurrent chunk uploads are safely locked with with_for_update
        so neither chunk is lost from received_chunks.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: sends concurrent HTTP requests and asserts received_chunks.
    """
    event_id, _, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    chunk0 = b"chunk_zero_data_bytes"
    chunk1 = b"chunk_one_data_bytes"
    full_data = chunk0 + chunk1
    full_sha = hashlib.sha256(full_data).hexdigest()

    init_res = organiser.post(
        f"/events/{event_id}/uploads/init",
        json={
            "filename": "concurrent.jpg",
            "byte_size": len(full_data),
            "total_chunks": 2,
            "expected_sha256": full_sha,
        },
    )
    assert init_res.status_code == 201
    upload_id = init_res.json()["upload_id"]

    c0_hash = hashlib.sha256(chunk0).hexdigest()
    c1_hash = hashlib.sha256(chunk1).hexdigest()

    def send_c0() -> int:
        r = organiser.post(
            f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=0&chunk_sha256={c0_hash}",
            content=chunk0,
            headers={"Content-Type": "application/octet-stream"},
        )
        return r.status_code

    def send_c1() -> int:
        r = organiser.post(
            f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=1&chunk_sha256={c1_hash}",
            content=chunk1,
            headers={"Content-Type": "application/octet-stream"},
        )
        return r.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        f0 = executor.submit(send_c0)
        f1 = executor.submit(send_c1)
        assert f0.result() == 200
        assert f1.result() == 200

    status_res = organiser.get(f"/events/{event_id}/uploads/{upload_id}")
    assert status_res.status_code == 200
    assert sorted(status_res.json()["received_chunks"]) == [0, 1]


def test_chunked_upload_complete_leaves_in_progress_and_chunks_if_ingest_fails(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that if upload_photos raises during complete_upload_session,
        the chunk folder is preserved on disk and the session status remains in_progress.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests failure handling during complete.
    """
    event_id, _, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    dummy_data = b"dummy_invalid_image_data"
    full_sha = hashlib.sha256(dummy_data).hexdigest()

    init_res = organiser.post(
        f"/events/{event_id}/uploads/init",
        json={
            "filename": "corrupt.jpg",
            "byte_size": len(dummy_data),
            "total_chunks": 1,
            "expected_sha256": full_sha,
        },
    )
    assert init_res.status_code == 201
    upload_id = init_res.json()["upload_id"]

    res_c0 = organiser.post(
        f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=0&chunk_sha256={full_sha}",
        content=dummy_data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert res_c0.status_code == 200

    chunk_dir = Path(ctx.settings.upload_spool_dir) / "chunks" / str(upload_id)
    assert chunk_dir.exists()

    with patch(
        "app.features.photo_upload.service.upload_photos",
        side_effect=UnprocessableError("simulated upload failure"),
    ):
        complete_res = organiser.post(f"/events/{event_id}/uploads/{upload_id}/complete")
        assert complete_res.status_code == 422

    # Verify chunk directory still exists on disk
    assert chunk_dir.exists()
    assert (chunk_dir / "0.chunk").exists()

    # Verify upload session is still in_progress in the database
    with event_transaction(ctx.sessions, event_id) as session:
        upload_sess = session.get(UploadSession, UUID(upload_id))
        assert upload_sess is not None
        assert upload_sess.status == "in_progress"


def test_chunked_upload_sha256_mismatch_persists_failed_status(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that a checksum mismatch marks the upload session as failed
        and persists the failed status to the database.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests failed status persistence.
    """
    event_id, _, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    actual_data = b"chunk_actual_content"
    actual_sha = hashlib.sha256(actual_data).hexdigest()
    mismatched_sha = "a" * 64

    init_res = organiser.post(
        f"/events/{event_id}/uploads/init",
        json={
            "filename": "mismatch.jpg",
            "byte_size": len(actual_data),
            "total_chunks": 1,
            "expected_sha256": mismatched_sha,
        },
    )
    assert init_res.status_code == 201
    upload_id = init_res.json()["upload_id"]

    res_c0 = organiser.post(
        f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=0&chunk_sha256={actual_sha}",
        content=actual_data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert res_c0.status_code == 200

    complete_res = organiser.post(f"/events/{event_id}/uploads/{upload_id}/complete")
    assert complete_res.status_code == 422
    assert "SHA-256 mismatch" in complete_res.json()["detail"]

    # Verify failed status persisted in database
    with event_transaction(ctx.sessions, event_id) as session:
        upload_sess = session.get(UploadSession, UUID(upload_id))
        assert upload_sess is not None
        assert upload_sess.status == "failed"


def test_chunk_init_total_chunks_and_complete_status_quarantined(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify chunk init returns total_chunks and complete returns status 'quarantined'.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: performs chunked upload workflow and verifies response fields.
    """
    event_id, _, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    photo_data = make_png(2, 1)
    photo_sha = hashlib.sha256(photo_data).hexdigest()

    init_res = organiser.post(
        f"/events/{event_id}/uploads/init",
        json={
            "filename": "sample.png",
            "byte_size": len(photo_data),
            "total_chunks": 1,
            "expected_sha256": photo_sha,
        },
    )
    assert init_res.status_code == 201
    init_json = init_res.json()
    assert "total_chunks" in init_json
    assert init_json["total_chunks"] == 1
    upload_id = init_json["upload_id"]

    chunk_res = organiser.post(
        f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=0&chunk_sha256={photo_sha}",
        content=photo_data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert chunk_res.status_code == 200

    complete_res = organiser.post(f"/events/{event_id}/uploads/{upload_id}/complete")
    assert complete_res.status_code == 200
    complete_json = complete_res.json()
    assert complete_json["status"] == "quarantined"
    assert complete_json["photo_id"] is not None
