"""Students are served only in cohort workspaces and Slack teams only in the Slack workspace."""

import base64
import os
from dataclasses import replace

import pytest

from openrouter_image_mcp.config import SLACK_WORKSPACE
from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.provisioning import (
    NOT_SERVED,
    KeyProvisioner,
    TeamKey,
)
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from tests.test_hosted_provisioning import ALICE, TEAM, WS, FakeManager


@pytest.fixture
def store(db_target):
    return StudentKeyStore(db_target, Vault("v1:" + base64.b64encode(os.urandom(32)).decode()))


@pytest.fixture
def manager():
    return FakeManager()


async def test_student_in_the_slack_workspace_is_not_served(store, manager):
    student = replace(ALICE, workspace_id=SLACK_WORKSPACE)
    provisioner = KeyProvisioner(store, manager, {WS}, team_workspaces={SLACK_WORKSPACE})
    with pytest.raises(AuthRequiredError, match=NOT_SERVED):
        await provisioner.ensure(student)
    assert manager.created == []


async def test_team_in_a_cohort_workspace_is_not_served(store, manager):
    team = replace(TEAM, workspace_id=WS)
    provisioner = KeyProvisioner(store, manager, {WS}, team_workspaces={SLACK_WORKSPACE})
    with pytest.raises(AuthRequiredError, match=NOT_SERVED):
        await provisioner.ensure(team, team=TeamKey("abbott"))
    assert manager.created == []


async def test_team_without_a_team_workspace_set_is_not_served(store, manager):
    with pytest.raises(AuthRequiredError, match=NOT_SERVED):
        await KeyProvisioner(store, manager, {WS}).ensure(TEAM, team=TeamKey("abbott"))
    assert manager.created == []


def test_overlapping_student_and_team_workspaces_are_refused(store, manager):
    with pytest.raises(ValueError, match="both"):
        KeyProvisioner(store, manager, {WS, SLACK_WORKSPACE}, team_workspaces={SLACK_WORKSPACE})


async def test_a_rejected_row_in_the_wrong_workspace_is_never_rechecked(store, manager):
    student = replace(ALICE, workspace_id=SLACK_WORKSPACE)
    team = replace(TEAM, workspace_id=WS)
    store.insert(student, "sk-or-v1-stray", "hs", "n")
    store.insert(team, "sk-or-v1-stray", "ht", "n")
    store.mark_rejected(student.key, SLACK_WORKSPACE)
    store.mark_rejected(team.key, WS)
    provisioner = KeyProvisioner(store, manager, {WS}, team_workspaces={SLACK_WORKSPACE})
    calls = []
    manager.get = lambda key_hash: calls.append(key_hash)
    with pytest.raises(AuthRequiredError, match=NOT_SERVED):
        await provisioner.ensure(student)
    with pytest.raises(AuthRequiredError, match=NOT_SERVED):
        await provisioner.ensure(team, team=TeamKey("abbott"))
    assert calls == [] and manager.created == [] and manager.deleted == []
