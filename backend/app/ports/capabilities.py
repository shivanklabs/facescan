"""Capabilities: what an adapter can do, so the registry can pick adapters by config."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Static description of an adapter. TODO: extend as ports are implemented."""

    adapter_name: str
    is_paid: bool = False
    sends_data_off_host: bool = False
    regions: tuple[str, ...] = ()
