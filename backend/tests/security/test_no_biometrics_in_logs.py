"""Invariants 3 and 7: selfie frames sent to /search are never written to disk, storage or
logs, and neither are their vectors; guest responses hold no vectors or scores."""

import base64
import builtins
import io
import logging
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2
import pytest
from app.core.services_setup import AppContext
from fastapi.testclient import TestClient
from tests.conftest import REPO, make_png, person_vector
from tests.helpers import (
    discover_search,
    frames_body,
    guest_client,
    searchable_event,
)

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


def test_search_keeps_selfie_frames_in_memory_only(
    organiser: TestClient,
    ctx: AppContext,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id, invite, _ = searchable_event(
        ctx, organiser, [make_png(1, 1, (70 + i, 50)) for i in range(3)], name="Privacy"
    )
    guest = guest_client(ctx, "guest-privacy@example.test", event_id, invite)
    frames = [make_png(1, 1, (333 + i, 222)) for i in range(3)]  # unique bytes
    body = frames_body(*frames)

    temp = tmp_path / "temp"
    temp.mkdir()
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    roots = [tmp_path, REPO / "var", REPO / "backend", REPO / "ai" / "accuracy_test"]
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
    response = discover_search(guest, body)
    monkeypatch.undo()
    after = snapshot(roots)

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"released", "events_with_photos", "events_searched"}
    assert writes == []
    assert after == before
    text = caplog.text + response.text
    assert not FLOAT_RUN.search(text)
    for frame in frames:
        assert base64.b64encode(frame[20:60]).decode() not in text
        assert frame[20:60].hex() not in text
    for value in person_vector(1, "auraface", 99)[:16]:
        assert f"{float(value):.6f}" not in text
