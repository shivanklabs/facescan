"""Jurisdiction and country policy loader (e.g. DPDP rules in settings/country-rules/in.yaml)."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class CountryPolicy:
    """Policy settings for an event country."""

    country: str
    minor_age: int
    minors_self_search: str
    search_window_days: int

    @property
    def minors_self_search_allowed(self) -> bool:
        """Purpose: check if guests under 18 can self-service search without guardian block."""
        return self.minors_self_search.strip().lower() == "allowed"


@lru_cache(maxsize=32)
def load_country_policy(country_code: str, policies_dir: str | Path | None = None) -> CountryPolicy:
    """
    Purpose: load and cache policy configuration for a 2-letter country code.
    Inputs: country_code (e.g. 'IN'), optional policies_dir path.
    Outputs: CountryPolicy instance.
    Side effects: reads yaml file from disk on cache miss.
    """
    if policies_dir is None:
        from app.core.config import get_settings

        dir_path = Path(get_settings().config_policies_dir)
    else:
        dir_path = Path(policies_dir)

    if not dir_path.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]
        if (repo_root / dir_path).is_dir():
            dir_path = repo_root / dir_path

    policy_file = dir_path / f"{country_code.strip().lower()}.yaml"
    if not policy_file.is_file():
        # Fallback to default policy if specific country file does not exist
        return CountryPolicy(
            country=country_code.upper(),
            minor_age=18,
            minors_self_search="restricted",
            search_window_days=30,
        )

    try:
        data: dict[str, Any] = yaml.safe_load(policy_file.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}

    return CountryPolicy(
        country=str(data.get("country", country_code.upper())),
        minor_age=int(data.get("minor_age", 18)),
        minors_self_search=str(data.get("minors_self_search", "restricted")),
        search_window_days=int(data.get("search_window_days", 30)),
    )
