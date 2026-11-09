"""The offline face pipeline: detect -> align -> quality -> embed (AuraFace + SFace).

Everything runs in memory on the CPU. Aligned crops are never stored or returned.
"""

from dataclasses import dataclass

from facescan_ai.alignment.align_5point import align_face
from facescan_ai.config import BLUR_DETECTOR_CONFIG, DetectorConfig
from facescan_ai.detection.yunet import YuNetDetector
from facescan_ai.embedding.auraface import create_auraface_embedder
from facescan_ai.embedding.base import FaceEmbedder
from facescan_ai.embedding.sface import create_sface_embedder
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.quality.base import FaceQuality
from facescan_ai.quality.basic_quality import BasicFaceQuality, BasicQualityConfig
from facescan_ai.types import Embedding, Face, ImageBGR, QualityScore

PRIMARY_ENGINE = "auraface"
SECOND_ENGINE = "sface"


class NoFaceFoundError(Exception):
    """The image contains no face that passes the detector settings."""


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

    def analyse_selfie(self, image: ImageBGR) -> tuple[AnalysedFace, int]:
        """
        Purpose: analyse the largest face in a selfie.
        Outputs: (that face, number of faces seen). Side effects: none (in memory only).
        Raises NoFaceFoundError when no face is found.
        """
        faces = self.detector.detect(image)
        if not faces:
            raise NoFaceFoundError("no face found in the selfie")
        face = max(faces, key=lambda f: f.bbox.width * f.bbox.height)
        aligned = align_face(image, face)
        embeddings = {e.engine: e.embed(aligned) for e in self.embedders}
        analysed = AnalysedFace(
            face=face, quality=self.quality.assess(face, aligned), embeddings=embeddings
        )
        return analysed, len(faces)
