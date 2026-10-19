"""Shared constants and per-workspace settings for openrouter-image-mcp.

Environment configuration for the hosted server lives in `hosted/settings.py`; it
builds one `Settings` per cohort workspace (`hosted/workspaces.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

API_BASE = "https://openrouter.ai/api/v1"
APP_URL = "https://github.com/skelly-77/openrouter-image-mcp"
APP_TITLE = "Northwestern AI Images"
# Where the retired local Sidecar kept each cohort's key; `export-local` still reads it.
KEYRING_SERVICE = "northwestern-openrouter-image-mcp"
COHORT_WORKSPACES = {
    "2027": "21082e84-ae02-4639-ad40-c7251b98ab10",
    "2028": "8804f9c5-afd2-4de3-8d9f-f74b3d58c651",
}
ADVISOR_MAX_TIMEOUT_S = 1800.0


@dataclass(frozen=True)
class Settings:
    workspace_id: str
    max_input_edge: int
    timeout_s: float
    ratio_tolerance: float
    advisor_timeout_s: float = ADVISOR_MAX_TIMEOUT_S

    def __post_init__(self) -> None:
        if not math.isfinite(self.advisor_timeout_s) or not 0 < self.advisor_timeout_s <= ADVISOR_MAX_TIMEOUT_S:
            raise ValueError(
                "advisor_timeout_s must be finite and greater than 0, "
                f"at most {ADVISOR_MAX_TIMEOUT_S:g} seconds."
            )
        if self.workspace_id not in COHORT_WORKSPACES.values():
            raise ValueError(
                "Settings workspace must belong to a configured Northwestern cohort."
            )

    @property
    def cohort(self) -> str:
        return next(
            year
            for year, workspace in COHORT_WORKSPACES.items()
            if workspace == self.workspace_id
        )
