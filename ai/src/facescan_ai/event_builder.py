"""Turns an index of faces into a matchable event: quality gate -> calibration -> clusters."""

import time
from dataclasses import dataclass, field, replace

import numpy as np

from facescan_ai.clustering.hdbscan_clusterer import ClusterConfig, cluster_event
from facescan_ai.index_store import EventIndex
from facescan_ai.matching.calibration import CalibrationConfig, calibrate
from facescan_ai.matching.matcher import PRIMARY_ENGINE, SECOND_ENGINE
from facescan_ai.matching.quality_gate import QualityGateConfig, is_weak


@dataclass(frozen=True, slots=True)
class EventBuildConfig:
    """All settings used when building an event."""

    gate: QualityGateConfig = field(default_factory=QualityGateConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    clustering: ClusterConfig = field(default_factory=ClusterConfig)


@dataclass(frozen=True, slots=True)
class BuildSummary:
    """Counts and timing of one build (no biometric data)."""

    faces: int
    weak_faces: int
    clusters: int
    single_face_clusters: int
    attached_weak: int
    unclustered_weak: int
    seconds: float
    calibration_lines: list[str]


def build_event(
    index: EventIndex,
    config: EventBuildConfig,
    must_link: tuple[tuple[int, int], ...] = (),
) -> BuildSummary:
    """
    Purpose: mark weak faces, calibrate thresholds and cluster the faces of one event.
    Inputs: index with faces (embeddings for both engines), config, confirmed same-person
        pairs (from accepted claims later; never from benchmark labels).
    Outputs: BuildSummary.
    Side effects: replaces index.faces (weak flags), index.calibration and index.clusters.
    """
    started = time.perf_counter()
    index.faces = [
        replace(f, weak=is_weak(f.bbox.short_side, f.quality, config.gate)) for f in index.faces
    ]
    photo_ids = [f.photo_id for f in index.faces]
    weak = [f.weak for f in index.faces]
    vectors = {
        engine: index.gallery(engine) if index.faces else np.zeros((0, 1), dtype=np.float32)
        for engine in (PRIMARY_ENGINE, SECOND_ENGINE)
    }
    index.calibration = calibrate(vectors, photo_ids, [not w for w in weak], config.calibration)
    index.clusters = (
        cluster_event(
            vectors,
            PRIMARY_ENGINE,
            photo_ids,
            weak,
            [f.quality for f in index.faces],
            config.clustering,
            must_link,
        )
        if index.faces
        else []
    )
    attached = sum(len(c.attached_weak) for c in index.clusters)
    return BuildSummary(
        faces=len(index.faces),
        weak_faces=sum(weak),
        clusters=len(index.clusters),
        single_face_clusters=sum(1 for c in index.clusters if len(c.members) == 1),
        attached_weak=attached,
        unclustered_weak=sum(weak) - attached,
        seconds=time.perf_counter() - started,
        calibration_lines=[c.describe() for c in index.calibration.values()],
    )
