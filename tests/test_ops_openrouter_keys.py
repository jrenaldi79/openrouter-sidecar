"""The instructor's key tool covers the students' cohort workspaces and the one Slack workspace."""

import argparse
import csv
import io

import httpx
import pytest

from openrouter_image_mcp.config import COHORT_WORKSPACES, SLACK_WORKSPACE
from openrouter_image_mcp.hosted.openrouter_keys import (
    BUDGET_GUARDRAIL,
    TEAM_GUARDRAIL,
    KeyManager,
)
from scripts.ops import openrouter_keys as tool

WS_2027, WS_2028 = COHORT_WORKSPACES["2027"], COHORT_WORKSPACES["2028"]


class Org:
    """The management API for three workspaces: keys and guardrails listed per `workspace_id`."""

    def __init__(self):
        self.keys = {
            WS_2027: [{"hash": "h1", "name": "Sidecar · ana@u.edu · 2027", "usage_monthly": 1.0}],
            WS_2028: [{"hash": "h2", "name": "Sidecar · bo@u.edu · 2028", "usage_monthly": 2.0}],
            SLACK_WORKSPACE: [{"hash": "h3", "name": "Sidecar · abbott · 2027", "usage_monthly": 4.0},
                              {"hash": "h4", "name": "Not ours", "usage_monthly": 9.0}],
        }
        self.guardrails = {
            WS_2027: [{"id": "g1", "name": BUDGET_GUARDRAIL, "limit_usd": 25}],
            WS_2028: [{"id": "g2", "name": BUDGET_GUARDRAIL, "limit_usd": 25}],
            SLACK_WORKSPACE: [{"id": "g3", "name": TEAM_GUARDRAIL, "limit_usd": 50}],
        }
        self.assignments = [{"key_hash": "h1", "guardrail_id": "g1"}, {"key_hash": "h2", "guardrail_id": "g2"},
                            {"key_hash": "h3", "guardrail_id": "g3"}]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, workspace = request.url.path, request.url.params.get("workspace_id")
        if path.endswith("/guardrails/assignments/keys"):
            rows = self.assignments
        elif path.endswith("/guardrails"):
            rows = self.guardrails[workspace]
        else:
            rows = self.keys[workspace]
        return httpx.Response(200, json={"data": rows, "total_count": len(rows)})


@pytest.fixture
async def manager():
    created = KeyManager("m" * 20, transport=httpx.MockTransport(Org().handle))
    yield created
    await created.aclose()


def args(**values):
    return argparse.Namespace(**({"cohort": None, "csv": False} | values))


async def test_list_covers_the_slack_workspace(manager, capsys):
    assert await tool.cmd_list(manager, args()) == 0
    shown = capsys.readouterr().out
    assert "ana@u.edu" in shown and "bo@u.edu" in shown and "abbott" in shown
    assert "Not ours" not in shown and "3 Sidecar keys" in shown
    assert [group for group, _ in await tool.sidecar_keys(manager, None)] == ["2027", "2028", "slack"]


async def test_cohort_filter_picks_one_workspace(manager):
    assert [key["hash"] for _, key in await tool.sidecar_keys(manager, "2028")] == ["h2"]
    assert [key["hash"] for _, key in await tool.sidecar_keys(manager, "slack")] == ["h3"]


async def test_usage_sums_each_group(manager, capsys):
    assert await tool.cmd_usage(manager, args()) == 0
    assert capsys.readouterr().out.splitlines() == [
        "2027: 1 keys, $1.00 this month", "2028: 1 keys, $2.00 this month",
        "slack (2027): 1 keys, $4.00 this month"]


async def test_a_team_is_found_by_its_label(manager):
    assert [key["hash"] for _, key in await tool.matching(manager, "Abbott", None)] == ["h3"]


async def test_guardrails_check_each_workspace_for_its_own_guardrail(manager, capsys):
    assert await tool.cmd_guardrails(manager, args()) == 0
    shown = capsys.readouterr().out
    assert f"slack (workspace {SLACK_WORKSPACE})" in shown
    assert f"{TEAM_GUARDRAIL}: $50.00 per key" in shown and f"{BUDGET_GUARDRAIL}: $25.00 per key" in shown
    assert "NO '" not in shown and "NOT ON THE BUDGET" not in shown
    assert shown.count("1 of 1 Sidecar keys on the budget") == 3


async def test_guardrails_flag_a_slack_workspace_without_the_team_guardrail(capsys):
    org = Org()
    org.guardrails[SLACK_WORKSPACE] = [{"id": "g1", "name": BUDGET_GUARDRAIL}]  # the student one is the wrong one
    manager = KeyManager("m" * 20, transport=httpx.MockTransport(org.handle))
    try:
        await tool.cmd_guardrails(manager, args(cohort="slack"))
    finally:
        await manager.aclose()
    shown = capsys.readouterr().out
    assert f"NO '{TEAM_GUARDRAIL}' GUARDRAIL" in shown and "2027 (workspace" not in shown
    assert "NOT ON THE BUDGET: Sidecar · abbott · 2027" in shown


def test_cohort_option_accepts_slack():
    assert tool._parser().parse_args(["usage", "--cohort", "slack"]).cohort == "slack"


def _two_classes_in_slack() -> Org:
    org = Org()
    org.keys[SLACK_WORKSPACE] += [{"hash": "h5", "name": "Sidecar · booth · 2028", "usage_monthly": 1.5},
                                  {"hash": "h6", "name": "Sidecar · cole · 2027", "usage_monthly": 0.5}]
    return org


async def _with(org, run):
    manager = KeyManager("m" * 20, transport=httpx.MockTransport(org.handle))
    try:
        return await run(manager)
    finally:
        await manager.aclose()


async def test_usage_breaks_the_slack_workspace_down_by_class(capsys):
    await _with(_two_classes_in_slack(), lambda manager: tool.cmd_usage(manager, args()))
    assert capsys.readouterr().out.splitlines() == [
        "2027: 1 keys, $1.00 this month", "2028: 1 keys, $2.00 this month",
        "slack (2027): 2 keys, $4.50 this month", "slack (2028): 1 keys, $1.50 this month"]


async def test_usage_csv_keeps_cohort_as_the_class_and_adds_the_group(capsys):
    await _with(_two_classes_in_slack(), lambda manager: tool.cmd_usage(manager, args(csv=True)))
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == ["cohort", "group", "email", "usage_monthly", "usage_total", "disabled", "created_at"]
    assert [row[:3] for row in rows[1:]] == [
        ["2027", "2027", "ana@u.edu"], ["2028", "2028", "bo@u.edu"], ["2027", "slack", "abbott"],
        ["2028", "slack", "booth"], ["2027", "slack", "cole"]]


def test_the_commands_talk_of_a_student_or_a_team(capsys):
    assert "team" in tool._parser().description.lower()
    assert tool._parser().parse_args(["show", "abbott"]).email == "abbott"
    with pytest.raises(SystemExit):
        tool._parser().parse_args(["show", "--help"])
    assert "email_or_team_label" in capsys.readouterr().out
