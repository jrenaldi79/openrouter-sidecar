import pytest

from openrouter_image_mcp import config
from openrouter_image_mcp.hosted.slack.registry import (
    DEFAULT_ALIASES,
    ChannelRegistry,
    ChannelRow,
    new_row,
)
from openrouter_image_mcp.preferences import ALIASES

PANEL = ("openai/gpt-x", "google/gemini-x")


def row(label="abbott", channel="C1", team="T1", cohort="2027", panel=PANEL, **optional):
    return new_row(team, channel, label, cohort, fusion_panel=panel,
                   fusion_analyst="openai/gpt-x", subagent_model="google/gemini-flash", **optional)


def test_new_row_defaults():
    r = row()
    assert r.workspace_id == config.COHORT_WORKSPACES["2027"]
    assert r.alias_map == DEFAULT_ALIASES and r.alias_map is not DEFAULT_ALIASES
    assert (r.daily_cap_usd, r.max_concurrent, r.active) == (4.0, 3, True)
    assert r.fetch_allowlist == () and r.invoker_allowlist == ()
    assert r.welcomed_at is None and r.warned_month is None and r.inactive_reason is None


def test_default_aliases_resolve():
    for brand in DEFAULT_ALIASES.values():
        assert brand.casefold() in ALIASES


def test_add_get_roundtrip(db_target):
    reg = ChannelRegistry(db_target)
    original = row(alias_map={"x": "ChatGPT"}, fetch_allowlist=("example.com", "a.org"),
                   invoker_allowlist=("U1", "U2"), daily_cap_usd=2.5, max_concurrent=2,
                   default_model="gemini")
    reg.add(original)
    assert reg.get("T1", "C1") == original
    assert reg.by_label("abbott") == original
    assert reg.all() == [original]
    assert reg.get("T1", "C2") is None and reg.by_label("nope") is None
    assert isinstance(reg.get("T1", "C1"), ChannelRow)


def test_add_rejects_bad_rows(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    bad = [row(label="other"),  # duplicate (team, channel)
           row(label="abbott", channel="C2"),  # duplicate label
           row(label="b", channel="C2", panel=()),
           row(label="b", channel="C2", panel=tuple(f"m/{i}" for i in range(9))),
           row(label="Bad Label", channel="C2"), row(label="", channel="C2"), row(label="a" * 41, channel="C2")]
    for r in bad:
        with pytest.raises(ValueError):
            reg.add(r)
    with pytest.raises(ValueError):
        row(cohort="1999")
    assert len(reg.all()) == 1


def test_update_fields(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    updated = reg.update("T1", "C1", daily_cap_usd=9.0, fetch_allowlist=("a.com",), default_model="grok")
    assert updated.daily_cap_usd == 9.0 and updated.fetch_allowlist == ("a.com",)
    assert reg.get("T1", "C1") == updated
    with pytest.raises(ValueError):
        reg.update("T1", "C1", fusion_panel=())
    with pytest.raises(ValueError):
        reg.update("T1", "C1", nonsense=1)
    with pytest.raises(ValueError):
        reg.update("T1", "missing", daily_cap_usd=1.0)


def test_deactivate_once(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    assert reg.deactivate("T1", "C1", "bot_removed") is True
    assert reg.deactivate("T1", "C1", "again") is False
    got = reg.get("T1", "C1")
    assert got.active is False and got.inactive_reason == "bot_removed"
    assert reg.deactivate("T1", "missing", "x") is False


def test_update_reactivates(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    reg.deactivate("T1", "C1", "guest")
    back = reg.update("T1", "C1", active=True, inactive_reason=None)
    assert back.active is True and back.inactive_reason is None


def test_mark_welcomed_once(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    assert [reg.mark_welcomed("T1", "C1"), reg.mark_welcomed("T1", "C1")] == [True, False]
    assert reg.get("T1", "C1").welcomed_at is not None


def test_mark_warned_once_per_month(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    assert reg.mark_warned("T1", "C1", "2026-10") is True
    assert reg.mark_warned("T1", "C1", "2026-10") is False
    assert reg.mark_warned("T1", "C1", "2026-11") is True
    assert reg.get("T1", "C1").warned_month == "2026-11"
