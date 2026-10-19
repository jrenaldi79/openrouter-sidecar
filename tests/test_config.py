"""Per-workspace image and advisor settings (the hosted server builds one per cohort)."""

import pytest

from openrouter_image_mcp import config
from openrouter_image_mcp.config import COHORT_WORKSPACES, Settings


def _settings(**overrides) -> Settings:
    values = {"workspace_id": COHORT_WORKSPACES["2027"], "max_input_edge": 2048, "timeout_s": 600.0,
              "ratio_tolerance": 0.03} | overrides
    return Settings(**values)


@pytest.mark.parametrize("cohort", sorted(COHORT_WORKSPACES))
def test_workspace_selects_its_cohort(cohort):
    settings = _settings(workspace_id=COHORT_WORKSPACES[cohort])
    assert settings.cohort == cohort
    assert settings.advisor_timeout_s == 1800


@pytest.mark.parametrize("workspace", ["", "old-firm"])
def test_unknown_workspace_is_rejected(workspace):
    with pytest.raises(ValueError, match="cohort"):
        _settings(workspace_id=workspace)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 1800.1])
def test_invalid_advisor_timeout_is_rejected(value):
    with pytest.raises(ValueError, match="advisor_timeout_s"):
        _settings(advisor_timeout_s=value)


def test_no_local_settings_remain():
    # Output folders and environment selection belonged to the retired stdio server.
    assert not hasattr(config, "load_settings") and not hasattr(config, "KEYRING_USER")
    assert "output_dir" not in Settings.__dataclass_fields__
