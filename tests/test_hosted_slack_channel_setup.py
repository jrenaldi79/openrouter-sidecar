from dataclasses import replace

import pytest

from openrouter_image_mcp.errors import (
    AuthRequiredError,
    BadRequestError,
    ProviderError,
)
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.audit import AdminAudit
from openrouter_image_mcp.hosted.admin.sessions import Session
from openrouter_image_mcp.hosted.slack.channel_setup import (
    ChannelRefused,
    audit_callback,
    diff,
    failure_reason,
    model_ids,
    same,
)
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row

ACTOR = Session("auth0|admin", "admin@northwestern.edu", "c" * 32)


def row(**optional):
    return new_row("T1", "C1", "abbott", "2027", fusion_panel=("google/a", "openai/b"),
                   fusion_analyst="openai/b", subagent_model="google/c", **optional)


def test_diff_lists_tracked_fields_that_differ_as_json_friendly_pairs():
    before = row()
    after = replace(before, fusion_panel=("google/a",), daily_cap_usd=2.0, welcomed_at=5.0, active=False)
    assert diff(before, after) == {"fusion_panel": [["google/a", "openai/b"], ["google/a"]],
                                   "daily_cap_usd": [4.0, 2.0], "active": [True, False]}
    assert diff(None, before)["team_label"] == [None, "abbott"]
    assert "slack_team_id" not in diff(None, before) and "created_at" not in diff(None, before)
    assert diff(before, before) == {}


def test_same_ignores_list_versus_tuple():
    assert same(["a", "b"], ("a", "b")) and same(4, 4.0) and same({"x": "y"}, {"x": "y"})
    assert not same(["a"], ("a", "b"))


def test_model_ids_skips_brands():
    found = model_ids({"fusion_panel": ["google/a"], "fusion_analyst": "openai/b",
                       "alias_map": {"g": "Gemini", "x": "z-ai/glm-x", "o": "open weight"}})
    assert found == {"google/a", "openai/b", "z-ai/glm-x"}
    assert model_ids({"daily_cap_usd": 2.0}) == set()


def test_failure_reason_is_plain_and_never_raw():
    current = row()
    assert failure_reason(AuthRequiredError("whatever"), current) == copy.NO_GUARDRAIL.format(cohort="2027").rstrip(".")
    assert failure_reason(BadRequestError("Choose a model."), current) == "Choose a model"
    down = failure_reason(ProviderError("key abc is down"), current)
    assert down == "OpenRouter didn't answer" and "abc" not in down
    assert "secret" not in failure_reason(RuntimeError("secret"), current)
    refused = ChannelRefused("Nothing changed.")
    assert str(refused) == "Nothing changed."


def test_audit_callback_writes_on_the_callers_connection(db_target):
    registry, audit = ChannelRegistry(db_target), AdminAudit(db_target, clock=lambda: 42.0)
    registry.add(row(), audit=audit_callback(audit, ACTOR, audit_log.REGISTERED))
    entry = audit.recent()[0]
    assert (entry.at, entry.action, entry.actor_email, entry.label) == (42.0, "registered", ACTOR.email, "abbott")
    assert entry.changes["daily_cap_usd"] == [None, 4.0]


def test_audit_callback_failure_rolls_the_row_back(db_target):
    registry = ChannelRegistry(db_target)

    def broken():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        registry.add(row(), audit=audit_callback(AdminAudit(db_target, clock=broken), ACTOR, audit_log.REGISTERED))
    assert registry.all() == []
