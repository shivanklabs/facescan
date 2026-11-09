"""Security test (invariants 3 and 7): `search` and `match` keep the selfie in memory.

Runs search on the local fixture selfie and asserts that
- no file is created, changed or deleted anywhere we can observe (repo folders, the index work
  folder, the fixtures folder, the current directory and a fresh temp folder that TMP, TEMP,
  TMPDIR and tempfile point to during the search), and no write-mode open() or cv2.imwrite
  happens during the search;
- captured logs and console output contain no embedding values and no image bytes.
"""

import base64
import builtins
import io
import logging
import os
import re
import secrets
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest
from facescan_ai import cli
from facescan_ai.image_io import read_image
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.pipeline import FacePipeline
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
FLOAT_RUN = re.compile(r"(-?\d+\.\d{3,}[,\s]+){8,}")


def snapshot(roots: list[Path]) -> dict[str, tuple[int, int]]:
    """Every file (path -> size, mtime) under the roots."""
    state: dict[str, tuple[int, int]] = {}

    def add(path: Path) -> None:
        try:
            st = path.stat()
        except OSError:
            return
        state[str(path)] = (st.st_size, st.st_mtime_ns)

    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
            for name in filenames:
                add(Path(dirpath) / name)
    return state


def guard_writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any write-mode open() or cv2.imwrite while the guard is active."""
    writes: list[str] = []
    real_open: Callable[..., object] = builtins.open

    def checked_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        if any(flag in mode for flag in "wax+"):
            writes.append(f"open({file!r}, {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    def no_imwrite(*args: object, **kwargs: object) -> bool:
        writes.append("cv2.imwrite")
        return False

    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(io, "open", checked_open)
    monkeypatch.setattr(cv2, "imwrite", no_imwrite)
    return writes


@pytest.fixture
def bench_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("FACESCAN_BENCH_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    work = tmp_path / "work"
    monkeypatch.setenv("FACESCAN_BENCH_WORK_DIR", str(work))
    yield work


@pytest.mark.parametrize("command", ["search", "match"])
def test_search_writes_nothing_and_logs_no_biometrics(
    command: str,
    single_face_selfie,
    model_registry: ModelRegistry,
    bench_env: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_faces = single_face_selfie
    runner = CliRunner()
    built = runner.invoke(cli.app, ["index", str(local_faces.folder), "--event", "sec"])
    assert built.exit_code == 0, built.output

    # Reference values to look for in logs/output (computed before the guarded search).
    selfie_bytes = local_faces.selfie.read_bytes()
    analysed = FacePipeline(model_registry).analyse_selfie(read_image(local_faces.selfie))
    vectors = {k: e.vector for k, e in analysed.embeddings.items()}

    temp = bench_env.parent / "temp"
    temp.mkdir()
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    roots = [REPO / "ai", REPO / "backend", REPO / "var", Path.cwd(), bench_env.parent]
    before = snapshot(roots)
    caplog.set_level(logging.DEBUG)
    writes = guard_writes(monkeypatch)
    result = runner.invoke(cli.app, [command, str(local_faces.selfie), "--event", "sec"])
    monkeypatch.undo()
    after = snapshot(roots)

    assert result.exit_code == 0, result.output
    assert writes == []
    assert after == before, {
        "created": sorted(set(after) - set(before)),
        "deleted": sorted(set(before) - set(after)),
        "changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
    }

    text = result.output + "\n" + caplog.text
    assert not FLOAT_RUN.search(text)
    for vector in vectors.values():
        for value in vector[:16]:
            assert f"{float(value):.6f}" not in text
        raw = np.asarray(vector, dtype="<f4").tobytes()
        assert base64.b64encode(raw[:24]).decode() not in text
    assert base64.b64encode(selfie_bytes[100:148]).decode() not in text
    assert selfie_bytes[100:148].hex() not in text
    assert "JFIF" not in text and "Exif" not in text


def test_match_path_in_memory_writes_nothing(
    local_faces,
    model_registry: ModelRegistry,
    bench_env: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guarantees as above, for the library calls behind `match`, using a single-face
    frame cropped from the fixture photo in memory (works while the fixture selfie shows
    several faces)."""
    from facescan_ai.event_builder import EventBuildConfig, build_event
    from facescan_ai.index_store import load_index, load_key, save_index, work_dir
    from facescan_ai.matching.matcher import MatchConfig, match_event
    from facescan_ai.matching.quality_gate import QualityGateConfig
    from facescan_ai.pipeline import SelfieFaceCountError

    pipeline = FacePipeline(model_registry)
    photo = read_image(local_faces.selfie)
    built = runner_index(pipeline, photo)
    build_event(built, EventBuildConfig())
    save_index(built, load_key(), work_dir())

    faces = pipeline.detector.detect(photo)
    largest = max(faces, key=lambda f: f.bbox.width * f.bbox.height)
    b = largest.bbox
    frame = np.ascontiguousarray(
        photo[
            max(0, int(b.y - 0.2 * b.height)) : int(b.y2 + 0.2 * b.height),
            max(0, int(b.x - 0.2 * b.width)) : int(b.x2 + 0.2 * b.width),
        ]
    )
    try:
        pipeline.analyse_selfie_frames([frame], QualityGateConfig())
    except SelfieFaceCountError:
        pytest.skip("could not crop a single-face frame from the fixture photo")

    temp = bench_env.parent / "temp"
    temp.mkdir()
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    roots = [REPO / "ai", REPO / "backend", REPO / "var", Path.cwd(), bench_env.parent]
    before = snapshot(roots)
    caplog.set_level(logging.DEBUG)
    writes = guard_writes(monkeypatch)
    query = pipeline.analyse_selfie_frames([frame], QualityGateConfig())
    result = match_event(
        load_index(built.event, load_key(), work_dir()), query.vectors, query.strong, MatchConfig()
    )
    text = caplog.text + repr(result) + repr(query)
    monkeypatch.undo()
    assert writes == []
    assert snapshot(roots) == before
    assert not FLOAT_RUN.search(text)
    for vector in query.vectors.values():
        for value in vector[:16]:
            assert f"{float(value):.6f}" not in text


def runner_index(pipeline: FacePipeline, photo: np.ndarray):
    """Build an in-memory event index from one photo (helper for the test above)."""
    from facescan_ai.index_store import EventIndex, IndexedFace

    index = EventIndex(event="secmem", engine_versions=pipeline.engine_versions, photos=["p.jpg"])
    for a in pipeline.analyse(photo):
        index.faces.append(
            IndexedFace(
                "p.jpg",
                a.face.bbox,
                a.face.landmarks,
                a.face.score,
                a.quality.value,
                {k: e.vector for k, e in a.embeddings.items()},
            )
        )
    return index
