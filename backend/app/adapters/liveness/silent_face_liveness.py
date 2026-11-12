"""Silent-Face Liveness adapter backed by facescan_ai.

Combines MiniFASNet passive anti-spoofing with YuNet landmark-based challenge
verification (eye blink and head turn) and frame quality gating.
All image processing and model inference happens strictly in memory.
"""

import threading

from facescan_ai.config import DetectorConfig
from facescan_ai.detection.yunet import YuNetDetector
from facescan_ai.image_io import ImageDecodeError, decode_image
from facescan_ai.liveness.silent_face import SilentFaceConfig, SilentFaceDetector
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.types import Face, ImageBGR

from app.ports.capabilities import Capabilities
from app.ports.liveness import LivenessVerdict


class SilentFaceLiveness:
    """Liveness adapter backed by Silent-Face MiniFASNet and YuNet."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        config: SilentFaceConfig | None = None,
    ) -> None:
        """
        Purpose: prepare adapter state; models loaded lazily on first use.
        Inputs: optional ModelRegistry and SilentFaceConfig.
        Outputs: none.
        Side effects: none.
        """
        self._registry = registry
        self._config = config
        self._detector: SilentFaceDetector | None = None
        self._yunet: YuNetDetector | None = None
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> Capabilities:
        """Static description: in-house Apache-2.0 Silent-Face ONNX models."""
        return Capabilities(adapter_name="silent_face")

    def _ensure_models(self) -> tuple[SilentFaceDetector, YuNetDetector]:
        """
        Purpose: thread-safe lazy loading of verified ONNX models.
        Inputs: none.
        Outputs: (SilentFaceDetector, YuNetDetector).
        Side effects: loads model weights.
        """
        with self._lock:
            if self._detector is None or self._yunet is None:
                reg = self._registry or ModelRegistry()
                self._detector = SilentFaceDetector(reg, config=self._config)
                det_spec, det_session = reg.create_session("yunet", threads=0)
                self._yunet = YuNetDetector(det_session, det_spec.version, DetectorConfig())
            return self._detector, self._yunet

    def verify_liveness(
        self, frames: list[bytes], front_only: bool = False, require_blink: bool = True
    ) -> LivenessVerdict:
        """
        Purpose: verify passive anti-spoofing and active challenges across frames.
        Inputs: encoded image bytes (in memory only), front_only flag, require_blink.
        Outputs: LivenessVerdict.
        Side effects: none.
        """
        detector, yunet = self._ensure_models()
        pairs: list[tuple[ImageBGR, Face]] = []
        for i, frame_bytes in enumerate(frames, start=1):
            try:
                img = decode_image(frame_bytes)
            except ImageDecodeError:
                return LivenessVerdict(
                    passed=False,
                    is_live=False,
                    blink_detected=False,
                    turn_detected=False,
                    silent_face_score=0.0,
                    reasons=[f"frame_{i}_decode_error"],
                )

            faces = yunet.detect(img)
            if len(faces) != 1:
                return LivenessVerdict(
                    passed=False,
                    is_live=False,
                    blink_detected=False,
                    turn_detected=False,
                    silent_face_score=0.0,
                    reasons=[f"frame_{i}_shows_{len(faces)}_faces"],
                )
            pairs.append((img, faces[0]))

        res = detector.evaluate_frames(pairs, front_only=front_only, require_blink=require_blink)
        return LivenessVerdict(
            passed=res.passed,
            is_live=res.is_live,
            blink_detected=res.blink_detected,
            turn_detected=res.turn_detected,
            silent_face_score=res.silent_face_score,
            reasons=res.reasons,
        )
