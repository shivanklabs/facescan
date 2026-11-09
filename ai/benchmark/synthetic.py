"""Synthetic benchmark events: random unit vectors instead of faces (no images of people).

Each person gets a random identity direction per engine; a face is that direction plus noise,
with more noise for small or low-quality faces, so genuine cosine scores spread roughly like
real ones (AuraFace about 0.4-0.8 for the same person, near 0.0-0.3 for strangers). A few
look-alike pairs share part of their direction. The labels are exact, so the benchmark
measures only the matching engine.

    uv run python ai/benchmark/synthetic.py <event>   # writes a demo dataset for the labeler
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from bench_dataset import DEFAULT_DATASETS_DIR, FaceLabel, Labels, dataset_paths, save_labels
from facescan_ai.index_store import EventIndex, IndexedFace, load_key, save_index, work_dir
from facescan_ai.types import BBox
from numpy.typing import NDArray
from run_benchmark import SelfieInput

DIMS = {"auraface": 512, "sface": 128}
NOISE = {"auraface": 0.5, "sface": 0.65}  # higher noise for the weaker engine


@dataclass(frozen=True, slots=True)
class SyntheticEvent:
    """A generated event: index with faces, exact labels and one selfie per person."""

    index: EventIndex
    labels: Labels
    selfies: dict[str, SelfieInput]


def _unit(v: NDArray[np.float64]) -> NDArray[np.float32]:
    """Purpose: L2-normalise to float32."""
    return (v / max(float(np.linalg.norm(v)), 1e-12)).astype(np.float32)


def make_synthetic_event(
    event: str = "synthetic",
    people: int = 30,
    photos: int = 400,
    strangers_per_photo: float = 1.5,
    seed: int = 7,
) -> SyntheticEvent:
    """
    Purpose: build a synthetic event.
    Inputs: event name, number of labelled people (each gets a selfie), photos, average
        unlabelled strangers per photo, random seed.
    Outputs: SyntheticEvent. Side effects: none.
    """
    rng = np.random.default_rng(seed)
    person_ids = [f"p{i:03d}" for i in range(people)]
    stranger_count = max(1, int(photos * strangers_per_photo / 3))
    identities: dict[str, dict[str, NDArray[np.float64]]] = {}
    for pid in person_ids + [f"s{i:04d}" for i in range(stranger_count)]:
        identities[pid] = {e: rng.normal(size=d) / np.sqrt(d) for e, d in DIMS.items()}
    for a, b in zip(person_ids[0:6:2], person_ids[1:6:2], strict=False):  # look-alike pairs
        for engine in DIMS:
            identities[b][engine] = 0.55 * identities[a][engine] + 0.45 * identities[b][engine]

    faces: list[IndexedFace] = []
    labels = Labels(event=event)
    photo_ids = [f"img_{i:04d}.jpg" for i in range(photos)]
    strangers = [p for p in identities if p.startswith("s")]
    for photo in photo_ids:
        present = list(rng.choice(person_ids, size=int(rng.integers(0, 4)), replace=False))
        present += list(
            rng.choice(strangers, size=int(rng.poisson(strangers_per_photo)), replace=False)
        )
        for k, pid in enumerate(present):
            size = float(np.clip(rng.lognormal(np.log(90), 0.6), 18, 400))
            quality = float(np.clip(rng.beta(5, 2) * min(1.0, size / 90), 0.02, 0.99))
            noise_scale = 1.0 + 1.6 * (1.0 - quality)
            embeddings = {
                e: _unit(
                    identities[pid][e] + rng.normal(size=d) / np.sqrt(d) * NOISE[e] * noise_scale
                )
                for e, d in DIMS.items()
            }
            bbox = BBox(40.0 + 420.0 * k, 60.0, size, size * 1.2)
            faces.append(
                IndexedFace(
                    photo_id=photo,
                    bbox=bbox,
                    landmarks=((0.0, 0.0),) * 5,  # not used by matching
                    detector_score=0.9,
                    quality=round(quality, 4),
                    embeddings=embeddings,
                )
            )
            labels.faces.append(FaceLabel(photo, bbox, pid if pid in person_ids else "unknown"))
    selfies = {
        pid: SelfieInput(
            vectors={
                e: _unit(identities[pid][e] + rng.normal(size=d) / np.sqrt(d) * NOISE[e] * 0.9)
                for e, d in DIMS.items()
            },
            strong=True,
        )
        for pid in person_ids
    }
    labels.complete_photos = set(photo_ids)  # synthetic labels are exact and complete
    index = EventIndex(
        event=event,
        engine_versions={"auraface": "synthetic", "sface": "synthetic"},
        photos=photo_ids,
        faces=faces,
    )
    return SyntheticEvent(index=index, labels=labels, selfies=selfies)


def write_demo_dataset(event: str, datasets_dir: Path = DEFAULT_DATASETS_DIR) -> Path:
    """
    Purpose: write a small synthetic dataset the labeler can open: placeholder photos (grey
        boxes where the faces are, no people), the encrypted index (built clusters) and
        labels.json with no labels yet.
    Side effects: writes datasets/<event>/photos/*.jpg and the index in the work folder.
    """
    from facescan_ai.event_builder import EventBuildConfig, build_event

    synthetic = make_synthetic_event(event=event, people=8, photos=40)
    build_event(synthetic.index, EventBuildConfig())
    paths = dataset_paths(event, datasets_dir)
    paths.photos.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    for photo in synthetic.index.photos:
        canvas = np.full((800, 1800, 3), 235, dtype=np.uint8)
        for face in synthetic.index.faces:
            if face.photo_id == photo:
                b = face.bbox
                shade = int(rng.integers(60, 200))
                cv2.rectangle(
                    canvas, (int(b.x), int(b.y)), (int(b.x2), int(b.y2)), (shade, shade, shade), -1
                )
        ok, encoded = cv2.imencode(".jpg", canvas)
        if ok:
            (paths.photos / photo).write_bytes(encoded.tobytes())
    save_index(synthetic.index, load_key(), work_dir())
    save_labels(Labels(event=event), paths.labels)
    return paths.root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python ai/benchmark/synthetic.py <event>")
    print(f"wrote {write_demo_dataset(sys.argv[1])}")
