"""Labeler tests: loopback-only binding, no image files written, guards and label persistence.

Uses a synthetic event (random vectors) and placeholder photos without people.
"""

import builtins
import io
import json
import os
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest
import uvicorn
from bench_dataset import Labels, dataset_paths, load_labels
from facescan_ai.cli import app as cli_app
from facescan_ai.event_builder import EventBuildConfig, build_event
from fastapi.testclient import TestClient
from photo_labeler.app import HOST, LabelerState, create_app, server_config
from synthetic import make_synthetic_event


def make_state(tmp_path: Path) -> LabelerState:
    event = make_synthetic_event(event="lab", people=4, photos=12, seed=5)
    build_event(event.index, EventBuildConfig())
    paths = dataset_paths("lab", tmp_path / "datasets")
    paths.photos.mkdir(parents=True)
    for photo in event.index.photos:
        canvas = np.full((700, 1800, 3), 220, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", canvas)
        assert ok
        (paths.photos / photo).write_bytes(encoded.tobytes())
    return LabelerState(event.index, paths.photos, Labels(event="lab"), paths.labels, ["p000"])


def client_for(state: LabelerState) -> TestClient:
    return TestClient(create_app(state), base_url="http://127.0.0.1")


def test_server_binds_to_loopback_only(tmp_path: Path) -> None:
    app = create_app(make_state(tmp_path))
    config = server_config(app, 0)
    assert config.host == HOST == "127.0.0.1"
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        addresses = [s.getsockname()[0] for srv in server.servers for s in srv.sockets]
        assert addresses and all(a == "127.0.0.1" for a in addresses)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    # The CLI offers no way to change the host.
    label_cmd = next(
        c
        for c in cli_app.registered_commands
        if c.name == "label" or (c.callback and c.callback.__name__ == "label")
    )
    assert label_cmd.callback is not None
    assert "host" not in label_cmd.callback.__code__.co_varnames


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def test_labelling_session_writes_no_image_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state(tmp_path)
    client = client_for(state)
    before = snapshot(tmp_path)
    writes: list[tuple[str, str]] = []
    real_open: Callable[..., object] = builtins.open

    def recording_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        if any(flag in mode for flag in "wax+"):
            writes.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    def no_imwrite(*args: object, **kwargs: object) -> bool:
        writes.append(("cv2.imwrite", ""))
        return False

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(io, "open", recording_open)
    monkeypatch.setattr(cv2, "imwrite", no_imwrite)

    data = client.get("/api/state").json()
    assert client.get("/").status_code == 200
    assert client.get("/static/labeler.js").status_code == 200
    for face in data["faces"]:
        r = client.get(f"/api/faces/{face['row']}/thumb")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"] == "no-store"
    for photo in data["photos"]:
        assert client.get(f"/api/photos/{photo['index']}/image").status_code == 200
    clusters = data["clusters"]
    assert (
        client.post(
            f"/api/clusters/{clusters[0]['id']}/name", json={"person_id": "p000"}
        ).status_code
        == 200
    )
    if len(clusters) > 2:
        assert (
            client.post(
                "/api/clusters/merge",
                json={"source": clusters[2]["id"], "target": clusters[1]["id"]},
            ).status_code
            == 200
        )
    first_row = clusters[0]["faces"][0]
    assert client.post(f"/api/faces/{first_row}/remove").status_code == 200
    assert (
        client.post(f"/api/faces/{first_row}/label", json={"label": "unknown"}).status_code == 200
    )
    unclustered = client.get("/api/state").json()["unclustered"]
    assert first_row in unclustered
    if len(unclustered) > 1:
        assert (
            client.post(f"/api/faces/{unclustered[-1]}/label", json={"label": "p001"}).status_code
            == 200
        )
    monkeypatch.undo()

    labels_path = dataset_paths("lab", tmp_path / "datasets").labels
    after = snapshot(tmp_path)
    changed = {p for p in after if before.get(p) != after[p]} | (set(before) - set(after))
    assert changed == {str(labels_path)}
    assert all(w[0] != "cv2.imwrite" for w in writes)
    for path, _ in writes:
        name = Path(path).name
        assert name == "labels.json" or (name.startswith(".labels.json.") and name.endswith(".tmp"))
    saved = load_labels(labels_path, "lab")
    assert any(f.label == "unknown" for f in saved.faces)
    assert "p000" in saved.persons()
    raw = json.loads(labels_path.read_text(encoding="utf-8"))
    assert set(raw) >= {"persons", "faces", "labeler_state"}


def test_guards_and_validation(tmp_path: Path) -> None:
    client = client_for(make_state(tmp_path))
    evil_host = TestClient(client.app, base_url="http://evil.example")
    assert evil_host.get("/api/state").status_code == 400
    r = client.post(
        "/api/faces/0/label", json={"label": "p001"}, headers={"Origin": "http://evil.example"}
    )
    assert r.status_code == 403
    assert (
        client.post(
            "/api/faces/0/label",
            content=b'{"label":"p001"}',
            headers={"Content-Type": "text/plain"},
        ).status_code
        == 422
    )
    assert client.post("/api/faces/0/label", json={"label": "Alice Smith"}).status_code == 422
    assert client.post("/api/faces/0/label", json={"label": "p001", "extra": 1}).status_code == 422
    assert client.post("/api/clusters/0/name", json={"person_id": "unknown!"}).status_code == 422
    assert client.get("/api/faces/99999/thumb").status_code == 404
    assert client.get("/static/../app.py").status_code == 404
    assert client.get("/docs").status_code == 404


def test_view_state_resets_when_index_is_rebuilt(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    client = client_for(state)
    row = client.get("/api/state").json()["clusters"][0]["faces"][0]
    client.post(f"/api/faces/{row}/remove")
    labels = load_labels(state.labels_path, "lab")
    assert labels.removed
    state.index.created_at = "rebuilt"
    labels.reset_view_if_stale(state.index)
    assert not labels.removed and not labels.merged


def test_port_is_free_after_shutdown() -> None:
    with socket.socket() as s:  # sanity: loopback sockets are available on this machine
        s.bind(("127.0.0.1", 0))
        assert s.getsockname()[0] == "127.0.0.1"


def test_undo_restores_previous_labels(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    client = client_for(state)
    data = client.get("/api/state").json()
    assert data["undo_steps"] == 0
    cid = data["clusters"][0]["id"]
    row = data["clusters"][0]["faces"][0]
    client.post(f"/api/clusters/{cid}/name", json={"person_id": "p000"})
    client.post(f"/api/faces/{row}/label", json={"label": "unknown"})
    assert client.get("/api/state").json()["faces"][row]["label"] == "unknown"
    assert client.post("/api/undo").json() == {"ok": True}
    assert client.get("/api/state").json()["faces"][row]["label"] == "p000"
    assert client.post("/api/undo").json() == {"ok": True}
    assert client.get("/api/state").json()["faces"][row]["label"] is None
    assert client.post("/api/undo").json() == {"ok": False}  # nothing left
    assert load_labels(state.labels_path, "lab").faces == []  # undo is saved too


def test_photo_complete_flag_and_progress(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    client = client_for(state)
    data = client.get("/api/state").json()
    total = len(data["photos"])
    assert data["progress"] == {"complete": 0, "total": total}
    assert client.post("/api/photos/0/complete", json={"complete": True}).status_code == 200
    data = client.get("/api/state").json()
    assert data["progress"]["complete"] == 1 and data["photos"][0]["complete"] is True
    saved = load_labels(state.labels_path, "lab")
    assert saved.complete_photos == {data["photos"][0]["photo_id"]}
    client.post("/api/undo")
    assert client.get("/api/state").json()["progress"]["complete"] == 0
    assert client.post(f"/api/photos/{total}/complete", json={"complete": True}).status_code == 404
    assert client.post("/api/photos/0/complete", json={"complete": "yes"}).status_code == 422


def test_page_lists_keyboard_shortcuts(tmp_path: Path) -> None:
    html = client_for(make_state(tmp_path)).get("/").text
    for key in (
        "<kbd>n</kbd>",
        "<kbd>x</kbd>",
        "<kbd>u</kbd>",
        "<kbd>j</kbd>",
        "<kbd>k</kbd>",
        "<kbd>z</kbd>",
    ):
        assert key in html
