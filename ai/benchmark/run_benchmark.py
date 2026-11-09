"""Benchmark runner: match every person's selfie against the event and score the releases.

For each person with a selfie (datasets/<event>/selfies/<person_id>.jpg) the selfie is matched
with the same code as `facescan-ai match`, and the released photos are compared with
labels.json. Reports (aggregate figures and person ids only, no names, no photos, no vectors)
go to ai/benchmark/reports/<event>_<timestamp>.md and .json. Per-photo debug output goes only to
the git-ignored ai/benchmark/work/.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bench_dataset import Labels
from facescan_ai.evaluation import (
    QUALITY_BUCKETS,
    SIZE_BUCKETS,
    Counts,
    bucket_of,
    compare,
    false_release_upper_bound,
    releases_needed,
)
from facescan_ai.index_store import EventIndex
from facescan_ai.matching.matcher import MatchConfig, match_event
from facescan_ai.types import FloatArray

PRECISION_CLAIM_RATE = 0.001  # 99.9% precision


@dataclass(frozen=True, slots=True, eq=False)
class SelfieInput:
    """A person's selfie query (in memory only)."""

    vectors: dict[str, FloatArray]
    strong: bool


@dataclass(slots=True)
class PersonReport:
    """Results for one person id."""

    person_id: str
    counts: Counts
    borderline: int
    borderline_true: int
    selfie_strong: bool
    cluster_margin_ok: bool


@dataclass(slots=True)
class BenchResult:
    """Everything the reports are built from."""

    event: str
    created_at: str
    persons: list[PersonReport]
    total: Counts
    upper_bound_95: float
    releases_needed_for_999: int
    recall_by_size: dict[str, tuple[int, int]]
    recall_by_quality: dict[str, tuple[int, int]]
    skipped: list[str]
    calibration: list[str]
    index_summary: dict[str, int]
    debug: list[dict[str, object]] = field(default_factory=list)


def evaluate(
    index: EventIndex,
    labels: Labels,
    selfies: dict[str, SelfieInput],
    config: MatchConfig,
) -> BenchResult:
    """
    Purpose: match each selfie and compare the released photos with the labels.
    Inputs: built event index, labels, person id -> selfie query, match config.
    Outputs: BenchResult (aggregates plus per-photo debug rows). Side effects: none.
    """
    truth = labels.persons()
    persons: list[PersonReport] = []
    skipped: list[str] = []
    size_hits: dict[str, list[int]] = {name: [0, 0] for name, _, _ in SIZE_BUCKETS}
    quality_hits: dict[str, list[int]] = {name: [0, 0] for name, _, _ in QUALITY_BUCKETS}
    debug: list[dict[str, object]] = []
    total = Counts(0, 0, 0)
    for person_id, selfie in sorted(selfies.items()):
        if person_id not in truth:
            skipped.append(f"{person_id}: selfie but no labelled photos")
            continue
        result = match_event(index, selfie.vectors, selfie.strong, config)
        released = set(result.release_photos)
        true_photos = set(truth[person_id])
        counts = compare(released, true_photos)
        total = total + counts
        persons.append(
            PersonReport(
                person_id=person_id,
                counts=counts,
                borderline=len(result.borderline_photos),
                borderline_true=len(set(result.borderline_photos) & true_photos),
                selfie_strong=selfie.strong,
                cluster_margin_ok=result.margin_ok,
            )
        )
        for face in index.faces:
            if labels.label_for(face.photo_id, face.bbox) != person_id:
                continue
            hit = 1 if face.photo_id in released else 0
            for hits, value, buckets in (
                (size_hits, face.bbox.short_side, SIZE_BUCKETS),
                (quality_hits, face.quality, QUALITY_BUCKETS),
            ):
                name = bucket_of(value, buckets)
                hits[name][0] += hit
                hits[name][1] += 1
        debug.append(
            {
                "person_id": person_id,
                "cluster_id": result.cluster_id,
                "cluster_band": str(result.cluster_band),
                "best_score": result.best_score,
                "second_score": result.second_score,
                "margin": result.margin,
                "released_true": sorted(released & true_photos),
                "released_false": sorted(released - true_photos),
                "missed": sorted(true_photos - released),
                "borderline": result.borderline_photos,
            }
        )
    for person_id in sorted(set(truth) - set(selfies)):
        skipped.append(f"{person_id}: labelled but no selfie")
    return BenchResult(
        event=index.event,
        created_at=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        persons=persons,
        total=total,
        upper_bound_95=false_release_upper_bound(total.false_releases, total.released),
        releases_needed_for_999=releases_needed(PRECISION_CLAIM_RATE),
        recall_by_size={k: (v[0], v[1]) for k, v in size_hits.items()},
        recall_by_quality={k: (v[0], v[1]) for k, v in quality_hits.items()},
        skipped=skipped,
        calibration=[c.describe() for c in index.calibration.values()],
        index_summary={
            "photos": len(index.photos),
            "faces": len(index.faces),
            "weak_faces": sum(1 for f in index.faces if f.weak),
            "clusters": len(index.clusters),
            "labelled_faces": len(labels.faces),
        },
        debug=debug,
    )


def _pct(value: float | None) -> str:
    """Purpose: percentage text or n/a."""
    return "n/a" if value is None else f"{100 * value:.2f}%"


def _ratio(hit: int, total: int) -> str:
    """Purpose: 'x/y (z%)' text."""
    return f"{hit}/{total} ({_pct(hit / total if total else None)})"


def report_json(result: BenchResult) -> dict[str, object]:
    """Purpose: aggregate report as JSON-ready data (no photo ids). Side effects: none."""
    return {
        "event": result.event,
        "created_at": result.created_at,
        "index": result.index_summary,
        "calibration": result.calibration,
        "total": {
            "released": result.total.released,
            "true_releases": result.total.true_releases,
            "false_releases": result.total.false_releases,
            "missed": result.total.missed,
            "precision": result.total.precision,
            "recall": result.total.recall,
            "false_release_rate_upper_95": result.upper_bound_95,
            "can_claim_99_9_precision": result.total.false_releases == 0
            and result.upper_bound_95 <= PRECISION_CLAIM_RATE,
            "releases_needed_for_99_9_claim": result.releases_needed_for_999,
        },
        "per_person": [
            {
                "person_id": p.person_id,
                "released": p.counts.released,
                "true_releases": p.counts.true_releases,
                "false_releases": p.counts.false_releases,
                "missed": p.counts.missed,
                "precision": p.counts.precision,
                "recall": p.counts.recall,
                "borderline": p.borderline,
                "borderline_true": p.borderline_true,
                "selfie_strong": p.selfie_strong,
                "margin_ok": p.cluster_margin_ok,
            }
            for p in result.persons
        ],
        "recall_by_face_size": {
            k: {"released": h, "total": t} for k, (h, t) in result.recall_by_size.items()
        },
        "recall_by_quality": {
            k: {"released": h, "total": t} for k, (h, t) in result.recall_by_quality.items()
        },
        "borderline_total": sum(p.borderline for p in result.persons),
        "borderline_true_total": sum(p.borderline_true for p in result.persons),
        "skipped": result.skipped,
    }


def report_markdown(result: BenchResult) -> str:
    """Purpose: human-readable aggregate report. Side effects: none."""
    t = result.total
    claim = t.false_releases == 0 and result.upper_bound_95 <= PRECISION_CLAIM_RATE
    lines = [
        f"# Benchmark: {result.event} ({result.created_at})",
        "",
        "Aggregate figures only: person ids, no names, photos or vectors.",
        "",
        "## Index",
        *[f"- {k}: {v}" for k, v in result.index_summary.items()],
        "",
        "## Calibration",
        *[f"- {line}" for line in result.calibration],
        "",
        "## Overall",
        f"- released photos: {t.released}",
        f"- precision: {_pct(t.precision)} ({t.true_releases} true, {t.false_releases} false)",
        f"- recall: {_pct(t.recall)} ({t.true_releases} of {t.truth}; {t.missed} missed)",
        f"- false-release rate, 95% upper bound: {_pct(result.upper_bound_95)}",
        f"- 99.9% precision claim supported: {'yes' if claim else 'no'} "
        f"(needs >= {result.releases_needed_for_999} released photos with 0 false releases)",
        f"- borderline photos (not released): {sum(p.borderline for p in result.persons)}, "
        f"of which correct: {sum(p.borderline_true for p in result.persons)}",
        "",
        "## Per person",
        "| person | released | false | missed | precision | recall | borderline (true) "
        "| selfie ok | margin ok |",
        "|---|---|---|---|---|---|---|---|---|",
        *[
            f"| {p.person_id} | {p.counts.released} | {p.counts.false_releases} "
            f"| {p.counts.missed} | {_pct(p.counts.precision)} | {_pct(p.counts.recall)} "
            f"| {p.borderline} ({p.borderline_true}) | "
            f"{'yes' if p.selfie_strong else 'no'} | {'yes' if p.cluster_margin_ok else 'no'} |"
            for p in result.persons
        ],
        "",
        "## Recall by face size (labelled faces)",
        *[f"- {k}: {_ratio(h, n)}" for k, (h, n) in result.recall_by_size.items()],
        "",
        "## Recall by face quality (labelled faces)",
        *[f"- {k}: {_ratio(h, n)}" for k, (h, n) in result.recall_by_quality.items()],
    ]
    if result.skipped:
        lines += ["", "## Skipped", *[f"- {s}" for s in result.skipped]]
    return "\n".join(lines) + "\n"


def write_reports(
    result: BenchResult, reports_dir: Path, work_dir: Path, prefix: str | None = None
) -> tuple[Path, Path, Path]:
    """
    Purpose: write the aggregate reports and the per-photo debug file.
    Outputs: (markdown, json, debug) paths.
    Side effects: writes reports_dir/<prefix>_<ts>.md/.json and work_dir/<prefix>_<ts>_debug.json.
    """
    name = f"{prefix or result.event}_{result.created_at}"
    reports_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    md = reports_dir / f"{name}.md"
    js = reports_dir / f"{name}.json"
    dbg = work_dir / f"{name}_debug.json"
    md.write_text(report_markdown(result), encoding="utf-8", newline="\n")
    js.write_text(json.dumps(report_json(result), indent=1) + "\n", encoding="utf-8", newline="\n")
    dbg.write_text(json.dumps(result.debug, indent=1) + "\n", encoding="utf-8", newline="\n")
    return md, js, dbg
