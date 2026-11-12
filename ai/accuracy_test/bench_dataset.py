"""Benchmark dataset layout and labels.json (git-ignored data, consented people only).

    ai/accuracy_test/datasets/<event>/photos/              event photos (indexed with facescan-ai)
    ai/accuracy_test/datasets/<event>/selfies/<person>.jpg one face each
    ai/accuracy_test/datasets/<event>/labels.json          ground truth (written by the labeler)

labels.json (format 1):
    {"format": 1, "event": "...", "updated_at": "...",
     "persons": {"p001": ["photo1.jpg", ...]},          photo-level ground truth (derived)
     "faces": [{"photo_id": "...", "bbox": [x, y, w, h], "label": "p001" | "unknown"}],
     "complete_photos": ["photo1.jpg", ...],            photos whose faces are all labelled
     "labeler_state": {"index_created_at": "...", "merged": {"5": 2}, "removed": ["<face key>"]}}
person ids are opaque (e.g. p001), never names. The benchmark counts only photos listed in
complete_photos, so an unlabelled face can never be scored as a false release.
"""

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from facescan_ai.encrypted_face_index import EventIndex, validate_event_name
from facescan_ai.types import BBox

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_DATASETS_DIR = BENCHMARK_DIR / "datasets"
DEFAULT_REPORTS_DIR = BENCHMARK_DIR / "reports"
PERSON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
UNKNOWN = "unknown"
LABELS_FORMAT = 1
MATCH_IOU = 0.5


class LabelsError(Exception):
    """labels.json is missing, malformed or inconsistent."""


def validate_person_id(person_id: str) -> str:
    """Purpose: allow only opaque ids like p001. Raises ValueError otherwise."""
    if person_id == UNKNOWN or not PERSON_ID_RE.fullmatch(person_id):
        raise ValueError("person_id must match [a-z0-9][a-z0-9_-]{0,31} and not be 'unknown'")
    return person_id


@dataclass(frozen=True, slots=True)
class DatasetPaths:
    """Folders and files of one benchmark event."""

    event: str
    root: Path

    @property
    def photos(self) -> Path:
        """Event photos."""
        return self.root / "photos"

    @property
    def selfies(self) -> Path:
        """One selfie per person: <person_id>.jpg."""
        return self.root / "selfies"

    @property
    def labels(self) -> Path:
        """Ground truth."""
        return self.root / "labels.json"

    def selfie_persons(self) -> dict[str, Path]:
        """Purpose: person id -> selfie path for valid ids. Side effects: lists the folder."""
        if not self.selfies.is_dir():
            return {}
        found = {}
        for path in sorted(self.selfies.iterdir()):
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and PERSON_ID_RE.fullmatch(
                path.stem
            ):
                found[path.stem] = path
        return found


def dataset_paths(event: str, datasets_dir: Path = DEFAULT_DATASETS_DIR) -> DatasetPaths:
    """Purpose: paths for an event (validates the name). Side effects: none."""
    return DatasetPaths(validate_event_name(event), datasets_dir / event)


def face_key(photo_id: str, bbox: BBox) -> str:
    """Purpose: stable text key of a face (photo id + rounded box). Side effects: none."""
    return f"{photo_id}|{round(bbox.x)},{round(bbox.y)},{round(bbox.width)},{round(bbox.height)}"


def iou(a: BBox, b: BBox) -> float:
    """Purpose: intersection over union of two boxes. Side effects: none."""
    x1, y1 = max(a.x, b.x), max(a.y, b.y)
    x2, y2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


@dataclass(slots=True)
class FaceLabel:
    """Ground-truth label of one face."""

    photo_id: str
    bbox: BBox
    label: str  # person id or "unknown"


@dataclass(slots=True)
class Labels:
    """Contents of labels.json."""

    event: str
    faces: list[FaceLabel] = field(default_factory=list)
    index_created_at: str = ""
    merged: dict[int, int] = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)
    updated_at: str = ""
    complete_photos: set[str] = field(default_factory=set)

    def persons(self, complete_only: bool = False) -> dict[str, list[str]]:
        """Photo-level ground truth: person id -> sorted photo ids (optionally only photos
        marked fully labelled)."""
        result: dict[str, set[str]] = {}
        for f in self.faces:
            if complete_only and f.photo_id not in self.complete_photos:
                continue
            if f.label != UNKNOWN:
                result.setdefault(f.label, set()).add(f.photo_id)
        return {p: sorted(photos) for p, photos in sorted(result.items())}

    def label_for(self, photo_id: str, bbox: BBox) -> str | None:
        """Purpose: label of the face overlapping bbox in that photo (IoU >= 0.5), if any."""
        best: tuple[float, str] | None = None
        for f in self.faces:
            if f.photo_id != photo_id:
                continue
            overlap = iou(f.bbox, bbox)
            if overlap >= MATCH_IOU and (best is None or overlap > best[0]):
                best = (overlap, f.label)
        return best[1] if best else None

    def set_label(self, photo_id: str, bbox: BBox, label: str | None) -> None:
        """
        Purpose: set, change or clear (None) the label of one face.
        Side effects: mutates self.faces.
        """
        self.faces = [
            f for f in self.faces if not (f.photo_id == photo_id and iou(f.bbox, bbox) >= MATCH_IOU)
        ]
        if label is not None:
            if label != UNKNOWN:
                validate_person_id(label)
            self.faces.append(FaceLabel(photo_id, bbox, label))

    def reset_view_if_stale(self, index: EventIndex) -> None:
        """Purpose: merges/removals refer to one index build; drop them for a new build."""
        if self.index_created_at != index.created_at:
            self.merged = {}
            self.removed = set()
            self.index_created_at = index.created_at


def load_labels(path: Path, event: str) -> Labels:
    """
    Purpose: read labels.json (an empty Labels if the file does not exist).
    Side effects: reads the file. Raises LabelsError on bad content.
    """
    if not path.is_file():
        return Labels(event=event)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["format"] != LABELS_FORMAT or raw["event"] != event:
            raise LabelsError("labels.json has another format or event")
        faces = []
        for f in raw["faces"]:
            x, y, w, h = (float(v) for v in f["bbox"])
            label = str(f["label"])
            if label != UNKNOWN:
                validate_person_id(label)
            faces.append(FaceLabel(str(f["photo_id"]), BBox(x, y, w, h), label))
        state = raw.get("labeler_state", {})
        return Labels(
            event=event,
            faces=faces,
            index_created_at=str(state.get("index_created_at", "")),
            merged={int(k): int(v) for k, v in state.get("merged", {}).items()},
            removed={str(k) for k in state.get("removed", [])},
            updated_at=str(raw.get("updated_at", "")),
            complete_photos={str(p) for p in raw.get("complete_photos", [])},
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LabelsError(f"labels.json is malformed: {type(exc).__name__}") from exc


def save_labels(labels: Labels, path: Path) -> None:
    """
    Purpose: write labels.json atomically (temp file + rename).
    Side effects: creates the folder and writes one file.
    """
    labels.updated_at = datetime.now(UTC).isoformat()
    payload = {
        "format": LABELS_FORMAT,
        "event": labels.event,
        "updated_at": labels.updated_at,
        "persons": labels.persons(),
        "faces": [
            {
                "photo_id": f.photo_id,
                "bbox": [
                    round(f.bbox.x, 1),
                    round(f.bbox.y, 1),
                    round(f.bbox.width, 1),
                    round(f.bbox.height, 1),
                ],
                "label": f.label,
            }
            for f in sorted(labels.faces, key=lambda f: (f.photo_id, f.bbox.x, f.bbox.y))
        ],
        "complete_photos": sorted(labels.complete_photos),
        "labeler_state": {
            "index_created_at": labels.index_created_at,
            "merged": {str(k): v for k, v in sorted(labels.merged.items())},
            "removed": sorted(labels.removed),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
