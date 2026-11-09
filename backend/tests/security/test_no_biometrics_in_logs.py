"""Invariants 3 and 7: a selfie sent to /match is never written to disk, storage or logs, and
neither are its vectors."""

import base64
import builtins
import io
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

import cv2
import pytest
from app.core.context import AppContext
from app.worker import run_one
from fastapi.testclient import TestClient
from tests.conftest import REPO, make_png, person_vector

FLOAT_RUN = re.compile(r"(-?\d+\.\d{3,}[,\s]+){8,}")


def snapshot(roots: list[Path]) -> dict[str, tuple[int, int]]:
    state: dict[str, tuple[int, int]] = {}
    skip = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
    for root in roots:
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in files:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                state[str(p)] = (st.st_size, st.st_mtime_ns)
    return state


def test_match_keeps_the_selfie_in_memory_only(
    api: TestClient,
    ctx: AppContext,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.integration.test_job_queue import drain

    drain(ctx)
    event_id = api.post("/events", json={"name": "Privacy", "country": "IN"}).json()["event_id"]
    files = [("files", (f"p{i}.png", make_png(1, 1, (70 + i, 50)), "image/png")) for i in range(3)]
    body = api.post(f"/events/{event_id}/photos", files=files).json()
    api.post(f"/events/{event_id}/collections/{body['collection_id']}/approve")
    while run_one(ctx, "test-worker"):
        pass

    selfie = make_png(1, 1, (333, 222))  # unique bytes
    temp = tmp_path / "temp"
    temp.mkdir()
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    roots = [tmp_path, REPO / "var", REPO / "backend", REPO / "ai" / "benchmark"]
    before = snapshot(roots)
    writes: list[str] = []
    real_open: Callable[..., object] = builtins.open

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        if any(flag in mode for flag in "wax+"):
            writes.append(f"{file!r} {mode}")
        return real_open(file, mode, *args, **kwargs)

    def no_imwrite(*args: object, **kwargs: object) -> bool:
        writes.append("cv2.imwrite")
        return False

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(cv2, "imwrite", no_imwrite)
    caplog.set_level(logging.DEBUG)
    response = api.post(
        f"/events/{event_id}/match", content=selfie, headers={"Content-Type": "image/png"}
    )
    monkeypatch.undo()
    after = snapshot(roots)

    assert response.status_code == 200, response.text
    assert writes == []
    assert after == before
    text = caplog.text + response.text
    assert not FLOAT_RUN.search(text)
    assert base64.b64encode(selfie[20:60]).decode() not in text
    assert selfie[20:60].hex() not in text
    vector = person_vector(1, "auraface", 99)
    for value in vector[:16]:
        assert f"{float(value):.6f}" not in text
    assert uuid.UUID(response.json()["event_id"])
