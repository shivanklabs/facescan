"""The offline face pipeline: detect -> align -> quality -> embed (AuraFace + SFace).

Everything runs in memory on the CPU. Aligned crops are never stored or returned.
"""

from dataclasses import dataclass

import numpy as np

from facescan_ai.alignment.align_5point import align_face
from facescan_ai.config import BLUR_DETECTOR_CONFIG, DetectorConfig
from facescan_ai.detection.yunet import YuNetDetector
from facescan_ai.embedding.auraface import create_auraface_embedder
from facescan_ai.embedding.base import FaceEmbedder, l2_normalise
from facescan_ai.embedding.sface import create_sface_embedder
from facescan_ai.matching.quality_gate import QualityGateConfig, is_weak
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.quality.base import FaceQuality
from facescan_ai.quality.basic_quality import BasicFaceQuality, BasicQualityConfig
from facescan_ai.types import Embedding, Face, FloatArray, ImageBGR, QualityScore

PRIMARY_ENGINE = "auraface"
SECOND_ENGINE = "sface"


class SelfieFaceCountError(Exception):
    """A selfie frame must contain exactly one face (security invariant 5: own face only)."""

    def __init__(self, count: int) -> None:
        """Purpose: keep the number of faces found for the message. Side effects: none."""
        self.count = count
        super().__init__(
            f"the selfie must show exactly one face; found {count}"
            + (" (move closer, face the camera)" if count == 0 else " (nobody else in frame)")
        )


def require_single_face(faces: list[Face]) -> Face:
    """
    Purpose: the selfie rule: exactly one face, otherwise refuse.
    Outputs: that face. Side effects: none. Raises SelfieFaceCountError.
    """
    if len(faces) != 1:
        raise SelfieFaceCountError(len(faces))
    return faces[0]


@dataclass(frozen=True, slots=True, eq=False)
class SelfieQuery:
    """Selfie embeddings (unit vectors per engine, mean over frames). In memory only."""

    vectors: dict[str, FloatArray]
    quality: QualityScore
    strong: bool
    frames: int

    def __repr__(self) -> str:
        """Purpose: debug text without vectors."""
        return f"SelfieQuery(frames={self.frames}, strong={self.strong})"


@dataclass(frozen=True, slots=True)
class AnalysedFace:
    """One face with its quality and embeddings (keyed by engine name)."""

    face: Face
    quality: QualityScore
    embeddings: dict[str, Embedding]


class FacePipeline:
    """Loads the verified models once and analyses images."""

    def __init__(
        self,
        registry: ModelRegistry,
        detector_config: DetectorConfig | None = None,
        quality: FaceQuality | None = None,
        threads: int = 0,
    ) -> None:
        """
        Purpose: create sessions for YuNet, AuraFace and SFace (SHA-256 verified).
        Inputs: registry, detector settings, quality assessor (default: basic), ONNX threads.
        Side effects: reads and hashes the model files.
        """
        config = detector_config or DetectorConfig()
        det_spec, det_session = registry.create_session("yunet", threads)
        self.detector = YuNetDetector(det_session, det_spec.version, config)
        self.blur_detector = YuNetDetector(det_session, det_spec.version, BLUR_DETECTOR_CONFIG)
        aura_spec, aura_session = registry.create_session(PRIMARY_ENGINE, threads)
        sface_spec, sface_session = registry.create_session(SECOND_ENGINE, threads)
        self.embedders: tuple[FaceEmbedder, ...] = (
            create_auraface_embedder(aura_session, aura_spec.version),
            create_sface_embedder(sface_session, sface_spec.version),
        )
        self.quality: FaceQuality = quality or BasicFaceQuality(
            BasicQualityConfig(min_face_px=config.min_face_px)
        )

    @property
    def engine_versions(self) -> dict[str, str]:
        """Engine name -> model version, plus the detector and quality assessor used."""
        versions = {e.engine: e.version for e in self.embedders}
        versions["detector"] = f"{self.detector.name}@{self.detector.version}"
        versions["quality"] = self.quality.name
        return versions

    def analyse(self, image: ImageBGR) -> list[AnalysedFace]:
        """
        Purpose: detect every face and compute quality and both embeddings.
        Inputs: BGR image. Outputs: one AnalysedFace per detected face.
        Side effects: none (in memory only).
        """
        results = []
        for face in self.detector.detect(image):
            aligned = align_face(image, face)
            embeddings = {e.engine: e.embed(aligned) for e in self.embedders}
            results.append(
                AnalysedFace(
                    face=face, quality=self.quality.assess(face, aligned), embeddings=embeddings
                )
            )
        return results

    def analyse_selfie(self, image: ImageBGR) -> AnalysedFace:
        """
        Purpose: analyse a single-face selfie frame.
        Outputs: the analysed face. Side effects: none (in memory only).
        Raises SelfieFaceCountError unless exactly one face is found.
        """
        face = require_single_face(self.detector.detect(image))
        aligned = align_face(image, face)
        embeddings = {e.engine: e.embed(aligned) for e in self.embedders}
        return AnalysedFace(
            face=face, quality=self.quality.assess(face, aligned), embeddings=embeddings
        )

    def analyse_selfie_frames(self, frames: list[ImageBGR], gate: QualityGateConfig) -> SelfieQuery:
        """
        Purpose: build the selfie query from one or more frames (milestone 2 passes one).
            Every frame must contain exactly one face. Vectors are averaged per engine and
            re-normalised; the selfie is strong only if every frame passes the quality gate.
        Outputs: SelfieQuery. Side effects: none (in memory only).
        Raises SelfieFaceCountError, or ValueError when no frames are given.
        """
        if not frames:
            raise ValueError("at least one selfie frame is required")
        analysed = [self.analyse_selfie(frame) for frame in frames]
        vectors = {
            engine: l2_normalise(
                np.mean([a.embeddings[engine].vector for a in analysed], axis=0).astype(np.float32)
            )
            for engine in analysed[0].embeddings
        }
        weakest = min(analysed, key=lambda a: a.quality.value)
        strong = all(not is_weak(a.face.bbox.short_side, a.quality.value, gate) for a in analysed)
        return SelfieQuery(
            vectors=vectors, quality=weakest.quality, strong=strong, frames=len(frames)
        )
