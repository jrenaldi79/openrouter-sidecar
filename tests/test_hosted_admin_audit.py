import pytest

from openrouter_image_mcp import storage
from openrouter_image_mcp.hosted.admin import audit
from openrouter_image_mcp.hosted.admin.audit import AdminAudit
from openrouter_image_mcp.hosted.admin.sessions import Session
from openrouter_image_mcp.hosted.slack.registry import new_row

ACTOR = Session(subject="auth0|a", email="a@example.edu", csrf_token="c" * 43)


def _row(label: str = "team-1"):
    return new_row("T1", "C1", label, "2027", fusion_panel=["m1"], fusion_analyst="m1", subagent_model="m1")


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self) -> float:
        self.now += 1
        return self.now


def test_audit_round_trip_newest_first(db_target):
    log = AdminAudit(db_target, clock=Clock())
    changes = {"daily_cap_usd": [4.0, 8.0], "fusion_panel": [["a"], ["a", "b"]]}
    log.record(ACTOR, audit.REGISTERED, _row("one"), changes)
    log.record(ACTOR, audit.TURNED_OFF, _row("two"), {})
    newest, oldest = log.recent()
    assert (newest.action, newest.label, newest.changes) == ("turned off", "two", {})
    assert oldest == audit.AuditEntry(at=1001.0, actor_email="a@example.edu", action="registered", team_id="T1",
                                      channel_id="C1", label="one", changes=changes)


def test_audit_same_instant_keeps_insert_order(db_target):
    log = AdminAudit(db_target, clock=lambda: 5.0)
    for label in ("one", "two", "three"):
        log.record(ACTOR, audit.CHANGED, _row(label), {})
    assert [e.label for e in log.recent()] == ["three", "two", "one"]


def test_audit_limit_200(db_target):
    log = AdminAudit(db_target, clock=Clock())
    for i in range(205):
        log.record(ACTOR, audit.CHANGED, _row(f"t{i}"), {})
    entries = log.recent()
    assert len(entries) == 200
    assert entries[0].label == "t204"
    assert len(log.recent(limit=3)) == 3


def test_insert_joins_caller_transaction(db_target):
    log = AdminAudit(db_target, clock=Clock())
    log.record(ACTOR, audit.CHANGED, _row("kept"), {})
    with pytest.raises(RuntimeError), storage.connect(db_target) as db:
        audit.insert(db, at=9.0, actor=ACTOR, action=audit.CHANGED, row=_row("lost"), changes={})
        raise RuntimeError("boom")
    assert [e.label for e in log.recent()] == ["kept"]


def test_insert_creates_its_table(db_target):
    with storage.connect(db_target) as db:
        audit.insert(db, at=9.0, actor=ACTOR, action=audit.REGISTERED, row=_row("first"), changes={})
    assert [e.label for e in AdminAudit(db_target).recent()] == ["first"]
