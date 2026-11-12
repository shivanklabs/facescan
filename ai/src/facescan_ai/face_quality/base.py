"""FaceQuality interface: every quality assessor (basic now, OFIQ later) implements it."""

from typing import Protocol

from facescan_ai.types import Face, ImageBGR, QualityScore


class FaceQuality(Protocol):
    """Scores how usable a detected face is for recognition, in [0, 1]."""

    name: str

    def assess(self, face: Face, aligned: ImageBGR) -> QualityScore:
        """
        Purpose: score one face.
        Inputs: detection (box, landmarks, score) and its aligned 112x112 crop.
        Outputs: QualityScore. Side effects: none.
        """
        ...
