"""Metric maths (including the 3/N bound), index format v2 and the benchmark runner."""

import json
import math
import secrets
from pathlib import Path

import numpy as np
import pytest
from bench_dataset import Labels, load_labels, save_labels
from facescan_ai.accuracy_maths import (
    QUALITY_BUCKETS,
    SIZE_BUCKETS,
    Counts,
    bucket_of,
    compare,
    false_release_upper_bound,
    releases_needed,
)
from facescan_ai.encrypted_face_index import (
    IndexDecryptionError,
    decrypt_index,
    encrypt_index,
    serialise,
)
from facescan_ai.event_builder import EventBuildConfig, build_event
from facescan_ai.face_matching.matcher import MatchConfig
from facescan_ai.types import BBox
from run_benchmark import evaluate, report_json, report_markdown, write_reports
from synthetic import make_synthetic_event


def test_counts_and_compare() -> None:
    c = compare({"a", "b", "x"}, {"a", "b", "c"})
    assert (c.true_releases, c.false_releases, c.missed) == (2, 1, 1)
    assert c.precision == pytest.approx(2 / 3)
    assert c.recall == pytest.approx(2 / 3)
    assert Counts(0, 0, 0).precision is None and Counts(0, 0, 0).recall is None
    assert (c + c).released == 6


def test_zero_false_releases_bound_is_about_three_over_n() -> None:
    for n in (100, 1000, 3000):
        bound = false_release_upper_bound(0, n)
        assert bound == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-12)
        assert bound == pytest.approx(3 / n, rel=0.02)  # "about 3/N"; exact for large N


def test_bound_with_false_releases_matches_clopper_pearson() -> None:
    # Known one-sided 95% Clopper-Pearson upper limits.
    assert false_release_upper_bound(1, 10) == pytest.approx(0.3942, abs=1e-3)
    assert false_release_upper_bound(2, 100) == pytest.approx(0.0617, abs=1e-3)
    assert false_release_upper_bound(5, 5) == 1.0
    assert false_release_upper_bound(0, 0) == 1.0
    assert false_release_upper_bound(1, 50) > false_release_upper_bound(0, 50)
    with pytest.raises(ValueError):
        false_release_upper_bound(3, 2)


def test_releases_needed_for_a_99_9_claim() -> None:
    n = releases_needed(0.001)
    assert n == 2995
    assert false_release_upper_bound(0, n) <= 0.001 < false_release_upper_bound(0, n - 1)


def test_buckets() -> None:
    assert bucket_of(10, SIZE_BUCKETS) == "<40px"
    assert bucket_of(40, SIZE_BUCKETS) == "40-80px"
    assert bucket_of(1e6, SIZE_BUCKETS) == ">=160px"
    assert bucket_of(0.69, QUALITY_BUCKETS) == "0.5-0.7"
    assert bucket_of(-math.inf, QUALITY_BUCKETS) == "<0.3"


@pytest.fixture(scope="module")
def built_event():
    event = make_synthetic_event(event="unit", people=10, photos=150, seed=3)
    build_event(event.index, EventBuildConfig())
    return event


def test_index_v2_round_trip_keeps_clusters_and_calibration(built_event) -> None:
    key = secrets.token_bytes(32)
    index = built_event.index
    restored = decrypt_index(encrypt_index(index, key), index.event, key)
    assert [c.members for c in restored.clusters] == [c.members for c in index.clusters]
    assert [c.attached_weak for c in restored.clusters] == [c.attached_weak for c in index.clusters]
    assert np.array_equal(
        restored.clusters[0].prototypes["auraface"], index.clusters[0].prototypes["auraface"]
    )
    assert restored.calibration == index.calibration
    assert [f.weak for f in restored.faces] == [f.weak for f in index.faces]


def test_index_v1_is_refused_with_a_hint(built_event) -> None:
    from facescan_ai import encrypted_face_index as index_store

    payload = json.loads(serialise(built_event.index))
    payload["format"] = 1
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = (
        index_store.MAGIC
        + nonce
        + AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), b"facescan-bench-index|v1|unit")
    )
    with pytest.raises(IndexDecryptionError, match="re-run"):
        decrypt_index(blob, "unit", key)


def test_evaluate_counts_are_consistent(built_event) -> None:
    result = evaluate(built_event.index, built_event.labels, built_event.selfies, MatchConfig())
    assert len(result.persons) == 10
    total = sum((p.counts for p in result.persons), Counts(0, 0, 0))
    assert total == result.total
    truth = built_event.labels.persons()
    assert result.total.truth == sum(len(v) for k, v in truth.items() if k in built_event.selfies)
    assert result.upper_bound_95 == false_release_upper_bound(
        result.total.false_releases, result.total.released
    )
    hit, n = map(sum, zip(*result.recall_by_size.values(), strict=True))
    assert n > 0 and 0 <= hit <= n


def test_reports_hold_aggregates_only(built_event, tmp_path: Path) -> None:
    result = evaluate(built_event.index, built_event.labels, built_event.selfies, MatchConfig())
    md, js, dbg = write_reports(result, tmp_path / "reports", tmp_path / "work", "synthetic_unit")
    assert md.parent == js.parent == tmp_path / "reports"
    assert dbg.parent == tmp_path / "work"
    for text in (md.read_text(encoding="utf-8"), js.read_text(encoding="utf-8")):
        assert ".jpg" not in text  # no photo ids in reports
        assert "img_" not in text
    assert "img_" in dbg.read_text(encoding="utf-8")  # per-photo detail only in work/
    data = report_json(result)
    assert data["total"]["can_claim_99_9_precision"] is False
    assert "95% upper bound" in report_markdown(result)


def test_labels_round_trip_and_persons(tmp_path: Path) -> None:
    labels = Labels(event="e1")
    labels.set_label("a.jpg", BBox(0, 0, 50, 50), "p001")
    labels.set_label("b.jpg", BBox(10, 10, 50, 50), "p001")
    labels.set_label("b.jpg", BBox(200, 10, 50, 50), "unknown")
    labels.set_label("a.jpg", BBox(2, 1, 50, 50), "p002")  # same face (IoU >= 0.5): relabel
    path = tmp_path / "labels.json"
    save_labels(labels, path)
    restored = load_labels(path, "e1")
    assert restored.persons() == {"p001": ["b.jpg"], "p002": ["a.jpg"]}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["persons"] == {"p001": ["b.jpg"], "p002": ["a.jpg"]}
    assert len(raw["faces"]) == 3
    with pytest.raises(ValueError):
        labels.set_label("a.jpg", BBox(0, 0, 50, 50), "Alice Smith")


def test_only_fully_labelled_photos_count() -> None:
    event = make_synthetic_event(event="partial", people=6, photos=80, seed=11)
    build_event(event.index, EventBuildConfig())
    labels = event.labels
    full = evaluate(event.index, labels, event.selfies, MatchConfig())
    assert full.photos_excluded == 0 and full.releases_excluded == 0

    # Pick a correctly released photo and forget the label of that person's face in it.
    person = next(p for p in full.persons if p.counts.true_releases > 0).person_id
    released = next(
        r["released_true"][0] for r in full.debug if r["person_id"] == person and r["released_true"]
    )
    labels.faces = [f for f in labels.faces if not (f.photo_id == released and f.label == person)]
    still_complete = evaluate(event.index, labels, event.selfies, MatchConfig())
    assert still_complete.total.false_releases == full.total.false_releases + 1

    # Not marked fully labelled: excluded, so the unlabelled face is not a false release.
    labels.complete_photos.discard(released)
    excluded = evaluate(event.index, labels, event.selfies, MatchConfig())
    assert excluded.total.false_releases == full.total.false_releases
    assert excluded.photos_excluded == 1
    assert excluded.releases_excluded >= 1
    data = report_json(excluded)
    assert data["photos_excluded_not_fully_labelled"] == 1
    assert "excluded (not fully labelled): 1" in report_markdown(excluded)


def test_nothing_counts_without_complete_photos() -> None:
    event = make_synthetic_event(event="none", people=3, photos=20, seed=2)
    build_event(event.index, EventBuildConfig())
    event.labels.complete_photos = set()
    result = evaluate(event.index, event.labels, event.selfies, MatchConfig())
    assert result.total.released == 0 and result.photos_counted == 0
    assert any("fully labelled" in s for s in result.skipped)
