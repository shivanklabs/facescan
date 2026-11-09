"""Encrypted per-event face index for the offline pipeline (benchmark use only).

One file per event: ai/benchmark/work/<event>.fsidx (git-ignored). It holds, per face: photo
id, bbox, landmarks, quality, weak flag and both embeddings; the event's clusters (membership
and prototypes per engine) and per-engine calibration; and the engine versions. It never holds
face crops or images. The file is AES-256-GCM encrypted with FACESCAN_BENCH_KEY (base64, 32 bytes);
the event name is authenticated as associated data, so a file cannot be renamed to another
event. Serialisation is JSON (no pickle), so loading cannot execute code.
"""

import base64
import binascii
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from facescan_ai.clustering.hdbscan_clusterer import EventCluster
from facescan_ai.matching.calibration import EngineCalibration
from facescan_ai.types import BBox, FloatArray, Landmarks

KEY_ENV = "FACESCAN_BENCH_KEY"
WORK_DIR_ENV = "FACESCAN_BENCH_WORK_DIR"
DEFAULT_WORK_DIR = Path(__file__).resolve().parents[2] / "benchmark" / "work"
EVENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FILE_SUFFIX = ".fsidx"
MAGIC = b"FSIDX\x01"
NONCE_BYTES = 12
KEY_BYTES = 32
FORMAT_VERSION = 2


class IndexStoreError(Exception):
    """Base error for the index store."""


class IndexKeyError(IndexStoreError):
    """FACESCAN_BENCH_KEY is missing or not a base64-encoded 32-byte key."""


class IndexNotFoundError(IndexStoreError):
    """No index file exists for the event."""


class IndexDecryptionError(IndexStoreError):
    """Wrong key, wrong event, or a corrupted/tampered file."""


@dataclass(frozen=True, slots=True, eq=False)
class IndexedFace:
    """One face in the index. Embeddings are biometric data: never log them."""

    photo_id: str
    bbox: BBox
    landmarks: Landmarks
    detector_score: float
    quality: float
    embeddings: dict[str, FloatArray]
    weak: bool = False

    def __repr__(self) -> str:
        """Purpose: debug text without vectors."""
        return f"IndexedFace(photo_id={self.photo_id!r}, engines={sorted(self.embeddings)})"


@dataclass(slots=True, eq=False)
class EventIndex:
    """All faces of one event plus how they were produced."""

    event: str
    engine_versions: dict[str, str]
    photos: list[str] = field(default_factory=list)
    faces: list[IndexedFace] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    clusters: list[EventCluster] = field(default_factory=list)
    calibration: dict[str, EngineCalibration] = field(default_factory=dict)

    def cluster_of(self) -> dict[int, int]:
        """Purpose: face row -> cluster id (members and attached weak faces)."""
        return {face: c.cluster_id for c in self.clusters for face in c.all_faces}

    def gallery(self, engine: str) -> FloatArray:
        """
        Purpose: N x D matrix of one engine's embeddings, in self.faces order.
        Side effects: none. Raises KeyError if a face lacks that engine.
        """
        if not self.faces:
            return np.zeros((0, 0), dtype=np.float32)
        return np.stack([f.embeddings[engine] for f in self.faces]).astype(np.float32)


def validate_event_name(event: str) -> str:
    """Purpose: allow only safe file-name event names. Raises ValueError otherwise."""
    if not EVENT_NAME_RE.fullmatch(event):
        raise ValueError("event name must match [a-z0-9][a-z0-9_-]{0,63}")
    return event


def load_key(environ: dict[str, str] | None = None) -> bytes:
    """
    Purpose: read FACESCAN_BENCH_KEY. Outputs: 32-byte key. Side effects: reads the env.
    Raises IndexKeyError when missing or malformed (never echoes the value).
    """
    env = os.environ if environ is None else environ
    raw = env.get(KEY_ENV, "").strip()
    if not raw:
        raise IndexKeyError(f"{KEY_ENV} is not set (run `uv run tasks setup`, then use .env)")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IndexKeyError(f"{KEY_ENV} is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise IndexKeyError(f"{KEY_ENV} must decode to {KEY_BYTES} bytes")
    return key


def work_dir(environ: dict[str, str] | None = None) -> Path:
    """Purpose: folder for index files (FACESCAN_BENCH_WORK_DIR or ai/benchmark/work)."""
    env = os.environ if environ is None else environ
    configured = env.get(WORK_DIR_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_WORK_DIR


def index_path(event: str, directory: Path) -> Path:
    """Purpose: file path of an event's index. Side effects: none."""
    return directory / f"{validate_event_name(event)}{FILE_SUFFIX}"


def _encode_vector(vector: FloatArray) -> str:
    """Purpose: float32 little-endian bytes as base64 text."""
    return base64.b64encode(np.asarray(vector, dtype="<f4").tobytes()).decode("ascii")


def _decode_vector(text: str) -> FloatArray:
    """Purpose: inverse of _encode_vector."""
    return np.frombuffer(base64.b64decode(text, validate=True), dtype="<f4").astype(np.float32)


def serialise(index: EventIndex) -> bytes:
    """Purpose: index -> JSON bytes (plaintext; only ever passed to encrypt)."""
    payload = {
        "format": FORMAT_VERSION,
        "event": index.event,
        "created_at": index.created_at,
        "engine_versions": index.engine_versions,
        "photos": index.photos,
        "faces": [
            {
                "photo_id": f.photo_id,
                "bbox": [f.bbox.x, f.bbox.y, f.bbox.width, f.bbox.height],
                "landmarks": [list(p) for p in f.landmarks],
                "detector_score": f.detector_score,
                "quality": f.quality,
                "weak": f.weak,
                "embeddings": {k: _encode_vector(v) for k, v in f.embeddings.items()},
            }
            for f in index.faces
        ],
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "members": c.members,
                "attached_weak": c.attached_weak,
                "prototypes": {k: _encode_vector(v) for k, v in c.prototypes.items()},
            }
            for c in index.clusters
        ],
        "calibration": {
            k: {
                "release_threshold": c.release_threshold,
                "borderline_threshold": c.borderline_threshold,
                "impostor_pairs": c.impostor_pairs,
                "release_calibrated": c.release_calibrated,
                "borderline_calibrated": c.borderline_calibrated,
                "impostor_max": c.impostor_max,
            }
            for k, c in index.calibration.items()
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def deserialise(data: bytes) -> EventIndex:
    """Purpose: JSON bytes -> index. Raises IndexDecryptionError on an unexpected layout."""
    try:
        payload = json.loads(data.decode("utf-8"))
        if payload["format"] != FORMAT_VERSION:
            raise IndexDecryptionError("index format is outdated; re-run `facescan-ai index`")
        faces = []
        for f in payload["faces"]:
            x, y, w, h = (float(v) for v in f["bbox"])
            pts = [(float(p[0]), float(p[1])) for p in f["landmarks"]]
            if len(pts) != 5:
                raise IndexDecryptionError("bad landmarks")
            faces.append(
                IndexedFace(
                    photo_id=str(f["photo_id"]),
                    bbox=BBox(x, y, w, h),
                    landmarks=(pts[0], pts[1], pts[2], pts[3], pts[4]),
                    detector_score=float(f["detector_score"]),
                    quality=float(f["quality"]),
                    embeddings={str(k): _decode_vector(v) for k, v in f["embeddings"].items()},
                    weak=bool(f["weak"]),
                )
            )
        n = len(faces)
        clusters = []
        for c in payload["clusters"]:
            members = [int(i) for i in c["members"]]
            weak_faces = [int(i) for i in c["attached_weak"]]
            if any(not 0 <= i < n for i in members + weak_faces):
                raise IndexDecryptionError("cluster refers to a missing face")
            clusters.append(
                EventCluster(
                    cluster_id=int(c["cluster_id"]),
                    members=members,
                    attached_weak=weak_faces,
                    prototypes={str(k): _decode_vector(v) for k, v in c["prototypes"].items()},
                )
            )
        calibration = {
            str(k): EngineCalibration(
                engine=str(k),
                release_threshold=float(c["release_threshold"]),
                borderline_threshold=float(c["borderline_threshold"]),
                impostor_pairs=int(c["impostor_pairs"]),
                release_calibrated=bool(c["release_calibrated"]),
                borderline_calibrated=bool(c["borderline_calibrated"]),
                impostor_max=None if c["impostor_max"] is None else float(c["impostor_max"]),
            )
            for k, c in payload["calibration"].items()
        }
        return EventIndex(
            event=str(payload["event"]),
            engine_versions={str(k): str(v) for k, v in payload["engine_versions"].items()},
            photos=[str(p) for p in payload["photos"]],
            faces=faces,
            created_at=str(payload["created_at"]),
            clusters=clusters,
            calibration=calibration,
        )
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise IndexDecryptionError("index content is malformed") from exc


def _aad(event: str) -> bytes:
    """Purpose: associated data binding the ciphertext to its event and format."""
    return b"facescan-bench-index|v1|" + event.encode("ascii")


def encrypt_index(index: EventIndex, key: bytes) -> bytes:
    """Purpose: MAGIC | nonce | AES-256-GCM(JSON). Outputs: file bytes. Side effects: none."""
    validate_event_name(index.event)
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, serialise(index), _aad(index.event))


def decrypt_index(blob: bytes, event: str, key: bytes) -> EventIndex:
    """
    Purpose: verify and decrypt index file bytes for an event.
    Outputs: EventIndex. Side effects: none.
    Raises IndexDecryptionError on a wrong key, wrong event or any tampering.
    """
    validate_event_name(event)
    if len(blob) < len(MAGIC) + NONCE_BYTES + 16 or not blob.startswith(MAGIC):
        raise IndexDecryptionError("not a FaceScan index file")
    nonce = blob[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    try:
        plain = AESGCM(key).decrypt(nonce, blob[len(MAGIC) + NONCE_BYTES :], _aad(event))
    except InvalidTag as exc:
        raise IndexDecryptionError("wrong key, wrong event or corrupted index") from exc
    index = deserialise(plain)
    if index.event != event:
        raise IndexDecryptionError("index belongs to another event")
    return index


def save_index(index: EventIndex, key: bytes, directory: Path) -> Path:
    """
    Purpose: write the encrypted index atomically (temp file in the same folder, then rename).
    Outputs: final path. Side effects: creates the folder and writes one file.
    """
    target = index_path(index.event, directory)
    directory.mkdir(parents=True, exist_ok=True)
    blob = encrypt_index(index, key)
    tmp = directory / f".{index.event}.{secrets.token_hex(8)}.tmp"
    try:
        with tmp.open("xb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def load_index(event: str, key: bytes, directory: Path) -> EventIndex:
    """Purpose: read and decrypt an event's index. Raises IndexNotFoundError if missing."""
    path = index_path(event, directory)
    try:
        blob = path.read_bytes()
    except FileNotFoundError as exc:
        raise IndexNotFoundError(f"no index for event '{event}'") from exc
    return decrypt_index(blob, event, key)


def delete_index(event: str, directory: Path) -> bool:
    """Purpose: delete an event's index. Outputs: True if a file was deleted."""
    path = index_path(event, directory)
    if path.exists():
        path.unlink()
        return True
    return False
