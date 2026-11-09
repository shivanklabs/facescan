"""Port: face analysis for one event (detect, embed, cluster, calibrate, match).

The backend talks to the face library only through this port (adapters/vision). Vectors in
these types are biometric data: they move between adapter and services in memory and are
stored only encrypted; they never appear in API responses or logs (invariants 3, 4 and 7).
"""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from app.ports.capabilities import Capabilities

Vector = NDArray[np.float32]
BoxTuple = tuple[float, float, float, float]  # x, y, width, height in original pixels


class SelfieRejectedError(Exception):
    """The selfie does not show exactly one face."""

    def __init__(self, face_count: int) -> None:
        """Purpose: keep the face count for the response. Side effects: none."""
        self.face_count = face_count
        super().__init__(f"the selfie must show exactly one face; found {face_count}")


class ImageRejectedError(Exception):
    """The bytes are not an allowed image (type, size or pixel limit)."""


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Facts about a validated image."""

    content_type: str
    width: int
    height: int


@dataclass(frozen=True, slots=True, eq=False)
class AnalysedFace:
    """One face found in a photo."""

    bbox: BoxTuple
    landmarks: tuple[tuple[float, float], ...]
    detector_score: float
    quality: float
    embeddings: dict[str, Vector]

    def __repr__(self) -> str:
        """Purpose: debug text without vectors."""
        return f"AnalysedFace(bbox={self.bbox}, quality={self.quality})"


@dataclass(frozen=True, slots=True, eq=False)
class StoredFace:
    """A face of the event as loaded (and decrypted) from the database."""

    face_id: UUID
    photo_id: UUID
    bbox: BoxTuple
    quality: float
    detector_score: float
    weak: bool
    embeddings: dict[str, Vector]

    def __repr__(self) -> str:
        """Purpose: debug text without vectors."""
        return f"StoredFace(face_id={self.face_id}, photo_id={self.photo_id})"


@dataclass(frozen=True, slots=True, eq=False)
class BuiltCluster:
    """A person-cluster: strong members, attached weak faces, prototypes per engine."""

    members: list[UUID]
    attached_weak: list[UUID]
    prototypes: dict[str, Vector]


@dataclass(frozen=True, slots=True, eq=False)
class BuiltEvent:
    """Result of clustering and calibrating one event."""

    weak: dict[UUID, bool]
    clusters: list[BuiltCluster]
    calibration: dict[str, dict[str, float | int | bool | None]]
    summary: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, eq=False)
class SelfieQuery:
    """A selfie's embeddings (in memory only) and whether it passed the quality gate."""

    vectors: dict[str, Vector]
    strong: bool

    def __repr__(self) -> str:
        """Purpose: debug text without vectors."""
        return f"SelfieQuery(strong={self.strong})"


@dataclass(frozen=True, slots=True)
class MatchBands:
    """Photo ids per band. No scores, no vectors."""

    release: list[UUID]
    borderline: list[UUID]


@dataclass(frozen=True, slots=True)
class FramesMatch:
    """Result of a multi-frame selfie search: released photo ids, and whether some photos
    may be missing (borderline matches, or frames that did not agree on one person)."""

    release: list[UUID]
    some_photos_missing: bool
    frames_agree: bool


class FaceAnalyzer(Protocol):
    """Face library operations used by ingest, indexing and matching."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...

    @property
    def engine_versions(self) -> dict[str, str]:
        """Engine name -> model version (stored with an event's calibration)."""
        ...

    def inspect_image(self, data: bytes, max_pixels: int) -> ImageInfo:
        """Purpose: validate type by content and pixel count. Raises ImageRejectedError."""
        ...

    def analyse_photo(self, data: bytes) -> list[AnalysedFace]:
        """Purpose: detect, score and embed every face of a photo. In memory only."""
        ...

    def web_copy(self, data: bytes, max_side: int) -> bytes:
        """Purpose: upright JPEG at most max_side pixels, with no EXIF or other metadata."""
        ...

    def full_copy(self, data: bytes) -> bytes:
        """Purpose: upright full-resolution JPEG with all metadata stripped (downloads)."""
        ...

    def selfie_query(self, data: bytes) -> SelfieQuery:
        """Purpose: embed a selfie in memory. Raises SelfieRejectedError unless one face."""
        ...

    def build_event(self, faces: list[StoredFace]) -> BuiltEvent:
        """Purpose: quality gate, calibration and clustering for one event's faces."""
        ...

    def match(
        self,
        faces: list[StoredFace],
        clusters: list[BuiltCluster],
        calibration: dict[str, dict[str, float | int | bool | None]],
        query: SelfieQuery,
    ) -> MatchBands:
        """Purpose: match one selfie against one event. Outputs: photo ids per band."""
        ...

    def match_frames(
        self,
        faces: list[StoredFace],
        clusters: list[BuiltCluster],
        calibration: dict[str, dict[str, float | int | bool | None]],
        frames: list[SelfieQuery],
    ) -> FramesMatch:
        """
        Purpose: match a multi-frame selfie. Every frame must pick the same cluster, otherwise
            nothing is released (borderline). Outputs: FramesMatch (no scores, no vectors).
        """
        ...
