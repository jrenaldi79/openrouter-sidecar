"""The Slack team principal: identity from the signed event and registry row only."""

import asyncio
import base64
import os
from dataclasses import replace

import pytest

from openrouter_image_mcp.advisor_store import AdvisorAccessError, Scope
from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.provisioning import NOT_READY
from openrouter_image_mcp.hosted.slack.principal import (
    SLACK_ISSUER,
    SlackCredentials,
    acting_as,
    team_principal,
)
from openrouter_image_mcp.hosted.slack.registry import new_row
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from tests import jwt_helpers as h
from tests.helpers import WS_2027


def _row(team="T1", channel="C1", label="abbott", **optional):
    return new_row(team, channel, label, "2027", fusion_panel=("openai/gpt-x",),
                   fusion_analyst="openai/gpt-x", subagent_model="google/gemini-flash", **optional)


@pytest.fixture
def store(db_target):
    return StudentKeyStore(db_target, Vault("v1:" + base64.b64encode(os.urandom(32)).decode()))


def test_principal_from_ids_and_row_only():
    principal = team_principal("T1", "C1", _row())
    assert principal.issuer == SLACK_ISSUER == "slack"
    assert principal.subject == "T1:C1"
    assert (principal.workspace_id, principal.cohort) == (WS_2027, "2027")
    assert principal.email is None
    for ids in (("T2", "C1"), ("T1", "C2")):
        with pytest.raises(ValueError):
            team_principal(*ids, _row())


def test_inactive_row_is_refused():
    with pytest.raises(ValueError):
        team_principal("T1", "C1", replace(_row(), active=False))


def test_team_key_never_matches_student():
    student = Principal(h.ISSUER, "T1:C1", WS_2027)
    assert team_principal("T1", "C1", _row()).key != student.key


def test_resolve_outside_context_refused(store):
    with pytest.raises(AuthRequiredError, match="No Slack channel in context"):
        SlackCredentials(WS_2027, store).resolve()


def test_forget_outside_context_refused(store):
    with pytest.raises(AuthRequiredError):
        SlackCredentials(WS_2027, store).forget()


def test_resolve_inside_context(store):
    principal = team_principal("T1", "C1", _row())
    credentials = SlackCredentials(WS_2027, store)
    with acting_as(principal):
        with pytest.raises(AuthRequiredError, match=NOT_READY):
            credentials.resolve()
        store.insert(principal, "sk-or-v1-team", "h1", "n")
        assert credentials.resolve() == ("sk-or-v1-team", Scope("slack|T1:C1", WS_2027))
    with pytest.raises(AuthRequiredError):  # the context is reset on exit
        credentials.resolve()


def test_context_resets_after_an_exception(store):
    principal = team_principal("T1", "C1", _row())
    with pytest.raises(RuntimeError), acting_as(principal):
        raise RuntimeError("boom")
    with pytest.raises(AuthRequiredError):
        SlackCredentials(WS_2027, store).resolve()


def test_other_workspace_is_refused(store):
    principal = team_principal("T1", "C1", _row())
    with acting_as(principal), pytest.raises(AdvisorAccessError):
        SlackCredentials("ws-other", store).resolve()


def test_check_and_forget_follow_the_store(store):
    principal = team_principal("T1", "C1", _row())
    store.insert(principal, "sk-or-v1-team", "h1", "n")
    credentials = SlackCredentials(WS_2027, store)
    scope = Scope(principal.key, WS_2027)
    credentials.check(scope)
    with acting_as(principal):
        credentials.forget()
    with pytest.raises(AdvisorAccessError):
        credentials.check(scope)


async def test_context_isolated_across_tasks(store):
    credentials = SlackCredentials(WS_2027, store)
    teams = [team_principal("T1", "C1", _row()), team_principal("T1", "C2", _row("T1", "C2", "booth"))]
    for team in teams:
        store.insert(team, "sk-or-v1-" + team.subject, "h-" + team.subject, "n")

    async def resolve(principal):
        with acting_as(principal):
            await asyncio.sleep(0.01)  # let the other task run inside its own context
            return credentials.resolve()

    first, second = await asyncio.gather(*(resolve(team) for team in teams))
    assert first[1].owner == "slack|T1:C1" and second[1].owner == "slack|T1:C2"
    assert first[0] != second[0]
