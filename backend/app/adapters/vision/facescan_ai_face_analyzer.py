"""FaceAnalyzer adapter backed by the facescan_ai library (ONNX Runtime, CPU).

Models are loaded lazily on first use (SHA-256 verified by facescan_ai's model registry), so
the API starts fast and only the worker and the match endpoint pay the load cost.
Images are decoded in memory; nothing here writes files.
"""

import io
import threading
import warnings
from uuid import UUID

import cv2
import numpy as np
from facescan_ai.clustering.hdbscan_clusterer import EventCluster
from facescan_ai.event_builder import EventBuildConfig, build_event
from facescan_ai.image_io import ImageDecodeError, decode_image
from facescan_ai.index_store import EventIndex, IndexedFace
from facescan_ai.matching.bands import Band
from facescan_ai.matching.calibration import EngineCalibration
from facescan_ai.matching.matcher import MatchConfig, match_event
from facescan_ai.matching.quality_gate import QualityGateConfig
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.pipeline import FacePipeline, SelfieFaceCountError
from facescan_ai.types import BBox
from PIL import Image, ImageOps, UnidentifiedImageError

from app.ports.capabilities import Capabilities
from app.ports.face_analyzer import (
    AnalysedFace,
    BuiltCluster,
    BuiltEvent,
    FramesMatch,
    ImageInfo,
    ImageRejectedError,
    SelfieQuery,
    SelfieRejectedError,
    StoredFace,
)

PRIMARY = "auraface"
_SIGNATURES: tuple[tuple[str, bytes, int], ...] = (
    ("image/jpeg", b"\xff\xd8\xff", 0),
    ("image/png", b"\x89PNG\r\n\x1a\n", 0),
    ("image/webp", b"WEBP", 8),
)
_PIL_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
_NO_LANDMARKS = ((0.0, 0.0),) * 5


def sniff_content_type(data: bytes) -> str | None:
    """Purpose: image type from magic bytes (never from the client's header)."""
    for content_type, magic, offset in _SIGNATURES:
        if data[offset : offset + len(magic)] == magic and (
            content_type != "image/webp" or data[:4] == b"RIFF"
        ):
            return content_type
    return None


def _calibration_to_dict(cal: EngineCalibration) -> dict[str, float | int | bool | None]:
    """Purpose: plain dict for encrypted storage."""
    return cal.to_dict()


def _calibration_from_dict(
    engine: str, raw: dict[str, float | int | bool | None]
) -> EngineCalibration:
    """Purpose: inverse of _calibration_to_dict."""
    return EngineCalibration.from_dict(engine, raw)


class FacescanAiFaceAnalyzer:
    """FaceAnalyzer on facescan_ai (YuNet, AuraFace, SFace, HDBSCAN)."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        """Purpose: remember where models come from; nothing is loaded yet."""
        self._registry = registry
        self._pipeline: FacePipeline | None = None
        self._lock = threading.Lock()
        self._gate = QualityGateConfig()

    @property
    def capabilities(self) -> Capabilities:
        """Static description: free, runs on this host."""
        return Capabilities(adapter_name="facescan_ai")

    def _get_pipeline(self) -> FacePipeline:
        """Purpose: load verified models once (thread-safe)."""
        with self._lock:
            if self._pipeline is None:
                self._pipeline = FacePipeline(self._registry or ModelRegistry())
            return self._pipeline

    @property
    def engine_versions(self) -> dict[str, str]:
        """Engine -> model version (loads the models if needed)."""
        return dict(self._get_pipeline().engine_versions)

    def inspect_image(self, data: bytes, max_pixels: int) -> ImageInfo:
        """
        Purpose: allow only real JPEG/PNG/WebP images within the pixel limit.
        Checks: magic bytes, the decoder agrees on the type, pixel count from the header,
        and oriented dimensions via EXIF transpose. Raises ImageRejectedError.
        Inputs: raw image bytes, maximum allowed pixels.
        Outputs: ImageInfo (content_type, width, height).
        Side effects: none (in memory only).
        """
        content_type = sniff_content_type(data)
        if content_type is None:
            raise ImageRejectedError("not a JPEG, PNG or WebP image")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as img:
                    if img.format != _PIL_FORMATS[content_type]:
                        raise ImageRejectedError("image content does not match its type")
                    if img.width * img.height > max_pixels:
                        raise ImageRejectedError("image has too many pixels")
                    oriented = ImageOps.exif_transpose(img)
                    width, height = oriented.size
        except ImageRejectedError:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as exc:
            raise ImageRejectedError("image cannot be read") from exc
        return ImageInfo(content_type=content_type, width=int(width), height=int(height))

    def analyse_photo(self, data: bytes) -> list[AnalysedFace]:
        """Purpose: every face of a photo with quality and embeddings (in memory)."""
        image = decode_image(data)
        faces = []
        for analysed in self._get_pipeline().analyse(image):
            b = analysed.face.bbox
            faces.append(
                AnalysedFace(
                    bbox=(b.x, b.y, b.width, b.height),
                    landmarks=analysed.face.landmarks,
                    detector_score=round(analysed.face.score, 4),
                    quality=analysed.quality.value,
                    embeddings={k: e.vector for k, e in analysed.embeddings.items()},
                )
            )
        return faces

    def web_copy(self, data: bytes, max_side: int) -> bytes:
        """Purpose: upright JPEG within max_side pixels; OpenCV writes no EXIF or metadata."""
        image = decode_image(data)
        scale = min(1.0, max_side / max(image.shape[:2]))
        if scale < 1.0:
            size = (round(image.shape[1] * scale), round(image.shape[0] * scale))
            image = np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_AREA), np.uint8)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ImageRejectedError("could not encode web copy")
        return bytes(encoded.tobytes())

    def full_copy(self, data: bytes) -> bytes:
        """Purpose: upright full-resolution JPEG; OpenCV writes no EXIF or other metadata."""
        image = decode_image(data)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise ImageRejectedError("could not encode download copy")
        return bytes(encoded.tobytes())

    def selfie_query(self, data: bytes) -> SelfieQuery:
        """Purpose: one-face selfie embeddings in memory. Raises SelfieRejectedError."""
        try:
            image = decode_image(data)
        except ImageDecodeError as exc:
            raise ImageRejectedError("selfie cannot be decoded") from exc
        try:
            query = self._get_pipeline().analyse_selfie_frames([image], self._gate)
        except SelfieFaceCountError as exc:
            raise SelfieRejectedError(exc.count) from exc
        return SelfieQuery(vectors=dict(query.vectors), strong=query.strong)

    @staticmethod
    def _index(faces: list[StoredFace], versions: dict[str, str]) -> EventIndex:
        """Purpose: facescan_ai EventIndex view of the stored faces (rows keep their order)."""
        return EventIndex(
            event="db-event",
            engine_versions=versions,
            photos=sorted({str(f.photo_id) for f in faces}),
            faces=[
                IndexedFace(
                    photo_id=str(f.photo_id),
                    bbox=BBox(*f.bbox),
                    landmarks=_NO_LANDMARKS,
                    detector_score=f.detector_score,
                    quality=f.quality,
                    embeddings=f.embeddings,
                    weak=f.weak,
                )
                for f in faces
            ],
        )

    def build_event(self, faces: list[StoredFace]) -> BuiltEvent:
        """Purpose: quality gate, calibration and clustering (facescan_ai defaults)."""
        index = self._index(faces, {})
        summary = build_event(index, EventBuildConfig(gate=self._gate))
        ids = [f.face_id for f in faces]
        return BuiltEvent(
            weak={ids[i]: f.weak for i, f in enumerate(index.faces)},
            clusters=[
                BuiltCluster(
                    members=[ids[i] for i in c.members],
                    attached_weak=[ids[i] for i in c.attached_weak],
                    prototypes=dict(c.prototypes),
                )
                for c in index.clusters
            ],
            calibration={k: _calibration_to_dict(v) for k, v in index.calibration.items()},
            summary={
                "faces": summary.faces,
                "weak_faces": summary.weak_faces,
                "clusters": summary.clusters,
            },
        )

    def _event_index(
        self,
        faces: list[StoredFace],
        clusters: list[BuiltCluster],
        calibration: dict[str, dict[str, float | int | bool | None]],
    ) -> EventIndex:
        """Purpose: facescan_ai EventIndex with clusters and calibration from storage."""
        index = self._index(faces, {})
        row_of = {f.face_id: i for i, f in enumerate(faces)}
        index.clusters = [
            EventCluster(
                cluster_id=k,
                members=[row_of[m] for m in c.members if m in row_of],
                attached_weak=[row_of[m] for m in c.attached_weak if m in row_of],
                prototypes=c.prototypes,
            )
            for k, c in enumerate(clusters)
            if PRIMARY in c.prototypes
        ]
        index.calibration = {k: _calibration_from_dict(k, v) for k, v in calibration.items()}
        return index

    def match_frames(
        self,
        faces: list[StoredFace],
        clusters: list[BuiltCluster],
        calibration: dict[str, dict[str, float | int | bool | None]],
        frames: list[SelfieQuery],
        front_only: bool = False,
        threshold_offset: float = 0.0,
    ) -> FramesMatch:
        """
        Purpose: match multi-frame selfie. In guided scan, front and side frames are matched
            separately; a photo is released only when frames agree on the same cluster and
            front-frame verdict releases. Side frames can find side-angle photos in the cluster.
            In front-only fallback, stricter calibrated release rules and margin are enforced.
        Inputs: stored faces, clusters, calibration, frames, front_only flag, threshold_offset.
        Outputs: FramesMatch. Side effects: none.
        """
        if not frames:
            raise ValueError("at least one frame is required")
        index = self._event_index(faces, clusters, calibration)

        if front_only:
            config = MatchConfig(min_margin=0.12, threshold_offset=threshold_offset)
            winners = {match_event(index, f.vectors, f.strong, config).cluster_id for f in frames}
            agree = len(winners) == 1 and None not in winners
            if not agree:
                return FramesMatch(release=[], some_photos_missing=True, frames_agree=False)
            combined = {}
            for engine in frames[0].vectors:
                mean = np.mean([f.vectors[engine] for f in frames], axis=0)
                norm = max(float(np.linalg.norm(mean)), 1e-12)
                combined[engine] = (mean / norm).astype(np.float32)
            result = match_event(index, combined, all(f.strong for f in frames), config)
            return FramesMatch(
                release=[UUID(p) for p in result.release_photos],
                some_photos_missing=bool(result.borderline_photos),
                frames_agree=True,
            )

        config = MatchConfig(min_margin=0.08, threshold_offset=0.0)
        front_frames = frames[:2] if len(frames) >= 2 else frames
        side_frames = frames[2:] if len(frames) > 2 else []

        front_combined = {}
        for engine in frames[0].vectors:
            mean = np.mean([f.vectors[engine] for f in front_frames], axis=0)
            norm = max(float(np.linalg.norm(mean)), 1e-12)
            front_combined[engine] = (mean / norm).astype(np.float32)
        front_result = match_event(
            index, front_combined, all(f.strong for f in front_frames), config
        )
        front_cluster = front_result.cluster_id

        if front_cluster is None or not front_result.release_photos:
            return FramesMatch(release=[], some_photos_missing=True, frames_agree=False)

        released_photos = set(front_result.release_photos)
        has_borderline = bool(front_result.borderline_photos)

        for side_f in side_frames:
            side_res = match_event(index, side_f.vectors, side_f.strong, config)
            if side_res.cluster_id != front_cluster or side_res.cluster_band is Band.NONE:
                return FramesMatch(release=[], some_photos_missing=True, frames_agree=False)
            for photo_id in side_res.release_photos:
                released_photos.add(photo_id)
            if side_res.borderline_photos:
                has_borderline = True

        return FramesMatch(
            release=[UUID(p) for p in released_photos],
            some_photos_missing=has_borderline,
            frames_agree=True,
        )
