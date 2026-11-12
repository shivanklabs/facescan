"""Port: Decides whether selfie frames come from a live person."""

from dataclasses import dataclass, field
from typing import Protocol

from app.ports.capabilities import Capabilities


@dataclass(frozen=True, slots=True)
class LivenessVerdict:
    """Outcome of liveness verification on a sequence of selfie frames."""

    passed: bool
    is_live: bool
    blink_detected: bool
    turn_detected: bool
    silent_face_score: float
    reasons: list[str] = field(default_factory=list)


class Liveness(Protocol):
    """Port: Decides whether selfie frames come from a live person."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...

    def verify_liveness(
        self, frames: list[bytes], front_only: bool = False, require_blink: bool = True
    ) -> LivenessVerdict:
        """
        Purpose: passive anti-spoofing + challenge checks (blink and head turn).
        Inputs: raw frame bytes (in memory only), front_only flag, require_blink (False only
            for the dev-only "look" scan of milestone 7; the anti-spoofing model still runs).
        Outputs: LivenessVerdict.
        Side effects: none.
        """
        ...
