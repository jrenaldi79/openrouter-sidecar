from dataclasses import replace

import pytest

from openrouter_image_mcp.errors import (
    KEY_REJECTED,
    AuthRequiredError,
    BadRequestError,
    NoGuardrailError,
    ProviderError,
)
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.audit import AdminAudit
from openrouter_image_mcp.hosted.admin.sessions import Session
from openrouter_image_mcp.hosted.provisioning import (
    DISABLED,
    NOT_READY,
    NOT_SERVED,
    NOT_SET_UP,
)
from openrouter_image_mcp.hosted.slack.channel_setup import (
    ChannelRefused,
    audit_callback,
    default_family,
    diff,
    failure_reason,
    model_ids,
    require_default_model,
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
    missing = copy.NO_GUARDRAIL.rstrip(".")
    assert failure_reason(NoGuardrailError("Couldn't create the key."), current) == missing
    assert failure_reason(BadRequestError("Choose a model."), current) == copy.MODEL_NOT_ACCEPTED
    down = failure_reason(ProviderError("key abc is down"), current)
    assert down == "OpenRouter didn't answer" and "abc" not in down
    assert "secret" not in failure_reason(RuntimeError("secret"), current)
    refused = ChannelRefused("Nothing changed.")
    assert str(refused) == "Nothing changed."


@pytest.mark.parametrize(("message", "sentence"), [
    (NOT_SERVED, copy.KEY_NOT_SERVED), (NOT_SET_UP, copy.KEY_NOT_SET_UP),
    (NOT_READY, copy.KEY_NOT_READY), (KEY_REJECTED, copy.KEY_REJECTED), (DISABLED, copy.KEY_DISABLED),
    ("secret detail nobody listed", copy.KEY_OTHER)])
def test_key_problems_are_worded_for_admins_not_students(message, sentence):
    reason = failure_reason(AuthRequiredError(message), row())
    assert reason == sentence
    assert "Your" not in reason and "instructor" not in reason and "secret" not in reason  # no student copy


def test_default_family_follows_the_default_alias():
    assert default_family(row()) == "google"  # the default alias gemini stands for the Gemini brand
    assert default_family(row(default_model="gpt")) == "openai"
    assert default_family(row(default_model="open")) == "open_weight"
    direct = row(alias_map={"fast": "google/gemini-flash"}, default_model="fast")
    assert default_family(direct) is None  # an exact model id needs no family model
    with pytest.raises(ChannelRefused) as refused:
        require_default_model(row(), {"openai": "openai/b", "google": None})
    assert str(refused.value) == copy.CHOOSE_DEFAULT_MODEL.format(family="google")
    require_default_model(row(), {"google": "google/a"})
    require_default_model(direct, {})


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
