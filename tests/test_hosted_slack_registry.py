import pytest

from openrouter_image_mcp import config, storage
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
    assert r.default_model == "gemini"
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
                   default_model="x")
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


@pytest.mark.parametrize("bad", [
    {"daily_cap_usd": float("inf")}, {"daily_cap_usd": float("nan")}, {"daily_cap_usd": 0.0},
    {"daily_cap_usd": -1.0}, {"max_concurrent": 0}, {"max_concurrent": -2},
    {"default_model": "claude"},  # not one of the channel's aliases
    {"alias_map": {"x": "ChatGPT"}},  # the default "gemini" is no longer an alias
])
def test_money_and_default_guards(db_target, bad):
    reg = ChannelRegistry(db_target)
    with pytest.raises(ValueError):
        reg.add(row(**bad))
    reg.add(row())
    with pytest.raises(ValueError):
        reg.update("T1", "C1", **bad)
    assert reg.get("T1", "C1") == row()


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


def test_update_does_not_undo_a_racing_guarded_write(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    stale, real_get, reads = reg.get("T1", "C1"), reg.get, []

    def get_once_stale(team_id, channel_id):
        reads.append(1)
        return stale if len(reads) == 1 else real_get(team_id, channel_id)

    reg.get = get_once_stale  # update's snapshot predates the three guarded writes below
    assert reg.mark_welcomed("T1", "C1") and reg.mark_warned("T1", "C1", "2026-10")
    assert reg.deactivate("T1", "C1", "guest")
    updated = reg.update("T1", "C1", daily_cap_usd=9.0)
    saved = real_get("T1", "C1")
    assert saved.daily_cap_usd == 9.0
    assert saved.welcomed_at is not None and saved.warned_month == "2026-10"
    assert saved.active is False and saved.inactive_reason == "guest"
    assert updated == saved


AUDIT_DDL = "CREATE TABLE IF NOT EXISTS test_audit (kind TEXT NOT NULL, before_label TEXT, after_label TEXT)"


@pytest.fixture
def audit_table(db_target):
    with storage.connect(db_target) as db:  # created up front so the rollback tests can read it afterwards
        db.execute(AUDIT_DDL)
    yield
    with storage.connect(db_target) as db:
        db.execute("DROP TABLE IF EXISTS test_audit")


def _recorder(kind):
    def audit(db, before, after):
        db.execute("INSERT INTO test_audit (kind, before_label, after_label) VALUES (?, ?, ?)",
                   (kind, before and before.team_label, after.team_label))
    return audit


def _entries(db_target):
    with storage.connect(db_target) as db:
        found = db.execute("SELECT kind, before_label, after_label FROM test_audit").fetchall()
    return [(r["kind"], r["before_label"], r["after_label"]) for r in found]


def _boom(db, before, after):
    db.execute("INSERT INTO test_audit (kind) VALUES ('lost')")
    raise RuntimeError("audit failed")


def test_audit_callback_commits_with_row(db_target, audit_table):
    reg = ChannelRegistry(db_target)
    reg.add(row(), audit=_recorder("add"))
    reg.update("T1", "C1", daily_cap_usd=9.0, audit=_recorder("update"))
    assert reg.deactivate("T1", "C1", "guest", audit=_recorder("deactivate")) is True
    assert reg.deactivate("T1", "C1", "again", audit=_recorder("deactivate")) is False  # no write, no entry
    assert _entries(db_target) == [("add", None, "abbott"), ("update", "abbott", "abbott"),
                                   ("deactivate", "abbott", "abbott")]
    assert reg.get("T1", "C1").inactive_reason == "guest"


def test_audit_callback_sees_before_and_after_rows(db_target):
    reg = ChannelRegistry(db_target)
    seen = []
    reg.add(row(), audit=lambda db, before, after: seen.append((before, after.daily_cap_usd)))
    reg.update("T1", "C1", daily_cap_usd=9.0, audit=lambda db, before, after: seen.append(
        (before.daily_cap_usd, after.daily_cap_usd)))
    reg.deactivate("T1", "C1", "guest", audit=lambda db, before, after: seen.append(
        (before.active, after.active, after.inactive_reason)))
    assert seen == [(None, 4.0), (4.0, 9.0), (True, False, "guest")]


def test_update_without_changes_writes_nothing_and_skips_audit(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(row())
    calls = []
    reg.update("T1", "C1", audit=lambda *args: calls.append(args))
    assert calls == []


def test_audit_callback_failure_rolls_back(db_target, audit_table):
    reg = ChannelRegistry(db_target)
    with pytest.raises(RuntimeError):
        reg.add(row(), audit=_boom)
    assert reg.get("T1", "C1") is None
    reg.add(row())
    with pytest.raises(RuntimeError):
        reg.update("T1", "C1", daily_cap_usd=9.0, audit=_boom)
    assert reg.get("T1", "C1").daily_cap_usd == 4.0
    with pytest.raises(RuntimeError):
        reg.deactivate("T1", "C1", "guest", audit=_boom)
    assert reg.get("T1", "C1").active is True
    assert _entries(db_target) == []
