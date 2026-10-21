"""Isolation between Slack teams (spec "Isolation and security"): stores, usage text, forged tool arguments,
and a static check that no query scopes by workspace or cohort without the owner."""

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from slack_dispatch_rig import DispatchRig
from slack_runner_rig import CHANNEL, body, call
from slack_tools_kit import msg

from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorAccessError, AdvisorStore, Scope
from openrouter_image_mcp.hosted.limits import Limits, RateLimitedError
from openrouter_image_mcp.hosted.slack.context import NameCache
from openrouter_image_mcp.hosted.slack.jobs import SlackJobs
from openrouter_image_mcp.hosted.slack.memory import ChannelMemory
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row
from openrouter_image_mcp.hosted.slack.threads import ThreadMap
from openrouter_image_mcp.hosted.slack.usage import usage_report
from openrouter_image_mcp.session_files import FilePacket, SessionFiles

SRC = Path(__file__).resolve().parents[1] / "src" / "openrouter_image_mcp"
THREAD = "1700000000.000001"
SECRET = "launch code for team B"


# ---- stores: team B's scope never sees team A's rows ----

@pytest.fixture
def teams(db_target):
    registry = ChannelRegistry(db_target)
    rows = [new_row("T1", channel, label, "2027", fusion_panel=("a/one",), fusion_analyst="d/an",
                    subagent_model="e/sub") for channel, label in (("C1", "team-a"), ("C2", "team-b"))]
    for row in rows:
        registry.add(row)
    return [Scope(f"slack|T1:{row.channel_id}", row.workspace_id) for row in rows], rows


def _fill(target, scope):
    """One of everything for a team: chat, session file, memory note, thread link, a finished job with spend."""
    store = AdvisorStore(target)
    chat = store.create(scope, "g/model", "Slack · team-a")
    SessionFiles(store).upsert(scope, chat.id, [FilePacket(path="slack/context.md", content=SECRET)])
    ChannelMemory(target).add(scope, SECRET, "U1")
    ThreadMap(target).put(scope, "C1", THREAD, "gemini", "inline", chat.id)
    results = AdvisorResults(store)
    job = results.create(scope, chat, "p", "proc", slack_user_id="U1", slack_channel_id="C1")
    results.update(scope, job["id"], "proc", status="completed", cost_usd=1.25, answer=SECRET)
    return chat, job


async def test_team_cannot_read_other_team_chats_memory_threads_jobs_usage(db_target, teams):
    (scope_a, scope_b), (_, row_b) = teams
    chat, job = _fill(db_target, scope_a)
    store, jobs, threads = AdvisorStore(db_target), SlackJobs(db_target), ThreadMap(db_target)
    results = AdvisorResults(store)

    assert [c.id for c in store.list(scope_a)] == [chat.id]
    assert store.list(scope_b) == []
    with pytest.raises(AdvisorAccessError):
        store.get(scope_b, chat.id)
    files = SessionFiles(store)
    assert [f["path"] for f in files.current(scope_a, chat.id)] == ["slack/context.md"]
    with pytest.raises(AdvisorAccessError):
        files.current(scope_b, chat.id)
    assert ChannelMemory(db_target).list(scope_b) == []
    assert ChannelMemory(db_target).instructions(scope_b) == ""
    assert threads.get(scope_b, "C1", THREAD, "gemini", "inline") is None
    assert threads.chats_in(scope_b, "C1") == [] and threads.thread_chat(scope_b, chat.id) is None
    assert threads.last_answered(scope_b, "C1", THREAD) is None
    with pytest.raises(AdvisorAccessError):
        results.get(scope_b, job["id"])
    assert jobs.running(scope_b, [chat.id]) == []
    assert jobs.spend_since(scope_b, "2000-01-01") == 0.0
    assert jobs.spend_by_user(scope_b, "2000-01-01") == []
    report = await usage_report({"usage_monthly": 0}, jobs, scope_b, row_b, NameCache(None),
                                datetime.now(UTC))
    assert report.today_spend == 0.0 and report.by_user == []
    jobs.mark_posted(scope_b, job["id"])  # a forged id from another team changes nothing
    with results.connection() as db:
        assert db.execute("SELECT slack_posted_at FROM advisor_jobs WHERE id=?", (job["id"],)).fetchone()["slack_posted_at"] is None


def test_team_spend_and_running_jobs_never_block_another_team(db_target, teams):
    (scope_a, scope_b), _ = teams
    _fill(db_target, scope_a)  # $1.25 spent today
    store = AdvisorStore(db_target)
    busy = store.create(scope_a, "g/model", "Slack · team-a")
    AdvisorResults(store).create(scope_a, busy, "p", "proc", slack_user_id="U1", slack_channel_id="C1")  # queued
    for caps, kind in (({"max_concurrent": 1, "daily_usd": 100.0}, "concurrency"),
                       ({"max_concurrent": 5, "daily_usd": 1.0}, "daily")):
        limits = Limits(store)
        with pytest.raises(RateLimitedError) as blocked:
            limits.admit(scope_a, **caps)
        assert blocked.value.kind == kind
        limits.admit(scope_b, **caps)  # team B shares the workspace, not team A's spend or slots


async def test_student_in_same_workspace_sees_no_team_data(db_target, teams):
    (scope_a, _), _ = teams
    chat, _ = _fill(db_target, scope_a)
    student = Scope("https://issuer.test/|email|alice", scope_a.workspace)
    assert AdvisorStore(db_target).list(student) == []
    assert ChannelMemory(db_target).list(student) == []
    assert SlackJobs(db_target).spend_since(student, "2000-01-01") == 0.0
    assert ThreadMap(db_target).thread_chat(student, chat.id) is None


# ---- end to end through the dispatcher ----

@pytest.fixture
def rig(db_target, keys):
    return DispatchRig(db_target, keys)


async def test_usage_has_no_key_text(rig):
    rig.key_info = {"usage_monthly": 2.5, "effective_budget": {"limit_usd": 20}, "label": "sk-or-v1-te...am",
                    "hash": "hash-team-abc", "name": "Sidecar · team-a · 2027", "key": "sk-or-v1-team"}
    before = len(rig.slack.posts)
    await rig.mention("usage")
    [text] = [p["text"] for p in rig.slack.posts[before:]]
    assert "$2.50 of $20.00" in text
    for secret in ("sk-or-v1", "hash-team-abc", "Sidecar · team-a", "slack|"):
        assert secret not in text


async def test_forged_channel_in_tool_args_ignored(rig):
    rig.slack.history["C1"] = [msg(1, "our own planning notes")]
    rig.slack.history["C2"] = [msg(2, SECRET)]
    reads, original = [], rig.slack.history._call

    async def recorded(channel, **kwargs):
        reads.append(channel)
        return await original(channel, **kwargs)

    rig.slack.history._call = recorded  # `history` is also the fake's message store, so wrap the call only
    forged = json.dumps({"channel": "C2", "limit": 5})

    def respond(sent):
        if len(rig.model.requests) == 1:
            return body(call("read_channel_history", "call_1", forged))
        return body()

    rig.model.respond = respond
    await rig.mention("summarize the channel", thread_ts=None)
    assert len(rig.model.requests) == 2
    assert reads and set(reads) == {CHANNEL}
    [output] = [item for item in rig.model.requests[1]["input"]
                if isinstance(item, dict) and item.get("type") == "function_call_output"]
    assert "our own planning notes" in output["output"]
    assert SECRET not in json.dumps(rig.model.requests)
    assert any(p["text"].startswith("The answer.") for p in rig.slack.posts)
    assert all(SECRET not in p["text"] for p in rig.slack.posts)


# ---- static: no query scopes by workspace or cohort without the owner ----

SCANNED = sorted((SRC / "hosted" / "slack").glob("*.py")) + [
    SRC / "advisor_results.py", SRC / "advisor_store.py", SRC / "session_files.py", SRC / "hosted" / "limits.py"]
ALLOWED = {
    # Keyed by Slack's own (team, channel) ids: the registry is the server's map from a signed event to its
    # principal, so it has no owner to bind. Its WHERE clauses use only slack_team_id, channel_id and label.
    ("registry.py", None),
    # Its SQL passes the scan today (it filters by process id, never by workspace or cohort); the entry records
    # the documented cross-team exception: the restart sweep reads every team's left-behind jobs, and `recover`
    # handles each row under that row's own team principal.
    ("jobs.py", "SlackJobs.orphans"),
}
_SQL = re.compile(r"\b(SELECT|UPDATE|DELETE|INSERT|CREATE)\b", re.IGNORECASE)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_CLAUSE_END = re.compile(r"\b(ORDER|GROUP|LIMIT)\b", re.IGNORECASE)
_GROUP_SCOPE = re.compile(r"workspace|cohort", re.IGNORECASE)
_OWNER = re.compile(r"\bowner\s*=", re.IGNORECASE)
UNKNOWN = "{?}"


def _assigns(node) -> dict:
    found = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
            found[child.targets[0].id] = child.value
    return found


def _text(node, env, depth=0) -> str | None:
    """The string an expression builds, with names resolved through `env`; None when it isn't a string."""
    if depth > 8:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name) and node.id in env:
        return _text(env[node.id], env, depth + 1)
    if isinstance(node, ast.JoinedStr):
        return "".join(_text(part.value if isinstance(part, ast.FormattedValue) else part, env, depth + 1)
                       or UNKNOWN for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, env, depth + 1), _text(node.right, env, depth + 1)
        return None if left is None and right is None else (left or UNKNOWN) + (right or UNKNOWN)
    return None


def _strings(node, env, qual, out) -> None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        qual = f"{qual}.{node.name}" if qual else node.name
        env = env if isinstance(node, ast.ClassDef) else env | _assigns(node)
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return  # a docstring
    if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
        value = _text(node, env)
        if value is not None:
            out.append((qual, value))
            return
    for child in ast.iter_child_nodes(node):
        _strings(child, env, qual, out)


def unscoped_queries(source: str) -> list[tuple[str | None, str]]:
    tree, found = ast.parse(source), []
    module_env = {k: v for k, v in _assigns(tree).items()}
    _strings(tree, module_env, None, found)
    bad = []
    for qual, text in found:
        if not (_WHERE.search(text) and _SQL.search(text)):
            continue
        clause = _CLAUSE_END.split(_WHERE.split(text, maxsplit=1)[1], maxsplit=1)[0]  # up to ORDER/GROUP/LIMIT
        scoped_by_group = _GROUP_SCOPE.search(clause) or UNKNOWN in clause
        if scoped_by_group and not _OWNER.search(clause):
            bad.append((qual, text))
    return bad


def test_scan_flags_a_workspace_only_query():
    source = ('SCOPE = "workspace=?"\n'
              'def leak(db):\n'
              '    db.execute(f"SELECT * FROM slack_memory WHERE {SCOPE}")\n'
              '    db.execute("SELECT * FROM advisor_jobs WHERE cohort=? AND id=?")\n'
              '    db.execute("select * from t where workspace=?")\n'
              '    db.execute("SELECT * FROM t WHERE workspace=? ORDER BY owner")\n'
              '    db.execute("SELECT * FROM t WHERE owner_hint IS NULL AND workspace=?")\n'
              '    db.execute("SELECT * FROM t WHERE owner=? AND workspace=?")\n'
              '    db.execute("SELECT * FROM t WHERE owner = ? AND workspace=? ORDER BY cohort")\n')
    assert [text for _, text in unscoped_queries(source)] == [
        "SELECT * FROM slack_memory WHERE workspace=?", "SELECT * FROM advisor_jobs WHERE cohort=? AND id=?",
        "select * from t where workspace=?", "SELECT * FROM t WHERE workspace=? ORDER BY owner",
        "SELECT * FROM t WHERE owner_hint IS NULL AND workspace=?"]


def test_no_query_filters_workspace_without_owner():
    problems = []
    for path in SCANNED:
        if (path.name, None) in ALLOWED:
            continue
        for qual, text in unscoped_queries(path.read_text(encoding="utf-8")):
            if (path.name, qual) not in ALLOWED:
                problems.append(f"{path.name}:{qual}: {text[:120]}")
    assert problems == []
    assert len(SCANNED) > 20 and all(path.is_file() for path in SCANNED)
