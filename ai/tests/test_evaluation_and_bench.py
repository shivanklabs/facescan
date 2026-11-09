"""Metric maths (including the 3/N bound), index format v2 and the benchmark runner."""

import json
import math
import secrets
from pathlib import Path

import numpy as np
import pytest
from bench_dataset import Labels, load_labels, save_labels
from facescan_ai.evaluation import (
    QUALITY_BUCKETS,
    SIZE_BUCKETS,
    Counts,
    bucket_of,
    compare,
    false_release_upper_bound,
    releases_needed,
)
from facescan_ai.event_builder import EventBuildConfig, build_event
from facescan_ai.index_store import (
    IndexDecryptionError,
    decrypt_index,
    encrypt_index,
    serialise,
)
from facescan_ai.matching.matcher import MatchConfig
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
    from facescan_ai import index_store

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
