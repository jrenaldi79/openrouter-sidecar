import asyncio

import pytest
from slack_admin_rig import (
    ACTOR,
    GUEST_TEXT,
    Rig,
    registered_row,
)

from openrouter_image_mcp.errors import (
    NoGuardrailError,
    ProviderError,
)
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.provisioning import UNAVAILABLE
from openrouter_image_mcp.hosted.slack.channel_admin import ChannelAdmin, ChannelRefused
from openrouter_image_mcp.hosted.slack.context import NameCache


@pytest.fixture
def rig(db_target):
    return Rig(db_target)


async def test_overview_lists_registered_and_unregistered(rig):
    rig.registry.add(registered_row())
    rig.slack.fail_next("channel_info", "boom")  # the one registered row falls back to its id
    overview = await rig.admin.overview()
    assert overview.registered == [(rig.registry.get("T1", "C1"), "C1")]
    assert overview.unregistered == [{"id": "C2", "name": "other", "is_private": True}]
    assert overview.slack_note is None
    assert (await rig.admin.overview()).registered[0][1] == "abbott"


async def test_overview_slack_off(rig):
    rig.registry.add(registered_row())
    admin = ChannelAdmin(rig.registry, None, None, rig.provisioner, rig.audit)
    overview = await admin.overview()
    assert overview.registered == [(rig.registry.get("T1", "C1"), "C1")]
    assert (overview.unregistered, overview.slack_note) == ([], copy.SLACK_OFF)


async def test_overview_when_slack_fails(rig):
    rig.registry.add(registered_row())
    rig.slack.fail_next("bot_channels", "ratelimited")
    overview = await rig.admin.overview()
    assert overview.registered == [(rig.registry.get("T1", "C1"), "C1")]
    assert (overview.unregistered, overview.slack_note) == ([], copy.SLACK_DOWN)


async def test_catalog_filters_like_students_and_reports_failure(rig):
    assert [m["id"] for m in await rig.admin.catalog()] == rig.client.ids
    rig.client.error = ProviderError("down")
    with pytest.raises(ChannelRefused) as refused:
        await rig.admin.catalog()
    assert str(refused.value) == copy.CATALOG_DOWN


async def test_turn_off_and_on_audited(rig):
    await rig.register()
    await rig.admin.turn_off("T1", "C1", "class ended", ACTOR)
    row = rig.registry.get("T1", "C1")
    assert (row.active, row.inactive_reason) == (False, "class ended")
    off = rig.audit.recent()[0]
    assert (off.action, off.changes) == (audit_log.TURNED_OFF,
                                         {"active": [True, False], "inactive_reason": [None, "class ended"]})
    with pytest.raises(ChannelRefused) as refused:
        await rig.admin.turn_off("T1", "C1", "again", ACTOR)
    assert str(refused.value) == copy.NOTHING_CHANGED
    await rig.admin.turn_on("T1", "C1", ACTOR)
    assert rig.registry.get("T1", "C1").active
    on = rig.audit.recent()[0]
    assert (on.action, on.changes) == (audit_log.TURNED_ON,
                                       {"active": [False, True], "inactive_reason": ["class ended", None]})
    with pytest.raises(ChannelRefused):
        await rig.admin.turn_on("T1", "C1", ACTOR)


async def test_turn_on_reruns_guest_check(rig):
    await rig.register()
    await rig.admin.turn_off("T1", "C1", "class ended", ACTOR)
    rig.slack.users["U2"] = {"is_restricted": True}
    with pytest.raises(ChannelRefused) as refused:
        await rig.admin.turn_on("T1", "C1", ACTOR)
    assert str(refused.value) == GUEST_TEXT
    assert not rig.registry.get("T1", "C1").active


async def test_turn_on_leaves_unfinished_setup_to_finish_setup(rig):
    rig.provisioner.error = NoGuardrailError(UNAVAILABLE)
    with pytest.raises(ChannelRefused):
        await rig.register()
    with pytest.raises(ChannelRefused):
        await rig.admin.turn_on("T1", "C1", ACTOR)
    assert not rig.registry.get("T1", "C1").active


async def test_two_turn_ons_at_once_make_one_entry(rig):
    await rig.register()
    await rig.admin.turn_off("T1", "C1", "class ended", ACTOR)
    results = await asyncio.gather(*(rig.admin.turn_on("T1", "C1", ACTOR) for _ in range(2)), return_exceptions=True)
    refused = [r for r in results if isinstance(r, ChannelRefused)]
    assert len(refused) == 1 and str(refused[0]) == copy.NOTHING_CHANGED
    assert rig.registry.get("T1", "C1").active
    assert rig.actions().count(audit_log.TURNED_ON) == 2  # setup's, and this one


async def test_members_cap_50_and_concurrency(rig):
    slack = rig.slack
    slack.members["C1"] = [f"U{i}" for i in range(12)]
    slack.users = {f"U{i}": {"profile": {"display_name": f"{'ba'[i % 2]}-{i:02d}"}} for i in range(12)}
    rig.admin.names = NameCache(slack)
    found = await rig.admin.members("C1")
    assert [name for _, name in found] == sorted(name for _, name in found) and len(found) == 12
    assert found[0] == ("U1", "a-01") and slack.peak == 5
    slack.members["C1"] = [f"U{i}" for i in range(50)]
    assert len(await rig.admin.members("C1")) == 50  # names missing from `users` fall back to the id
    slack.members["C1"] = [f"U{i}" for i in range(51)]
    assert await rig.admin.members("C1") is None
    slack.members["C1"] = ["U1"]
    slack.fail_next("members", "boom")
    assert await rig.admin.members("C1") is None
    assert await ChannelAdmin(rig.registry, None, None, rig.provisioner, rig.audit).members("C1") is None


async def test_channel_returns_row_and_saved_models(rig):
    assert await rig.admin.channel("T1", "C1") is None
    await rig.register()
    row, saved = await rig.admin.channel("T1", "C1")
    assert row == rig.registry.get("T1", "C1")
    assert saved["google"] == "google/gemini-x" and saved["openai"] == "openai/gpt-x"


async def test_channel_without_readable_models_says_so(rig):
    rig.registry.add(registered_row())
    admin = ChannelAdmin(rig.registry, rig.slack, None, rig.provisioner, rig.audit)  # no advisor services
    row, saved = await admin.channel("T1", "C1")
    assert row.team_label == "abbott" and saved is None  # not {}: that would read as "nothing saved"


async def test_is_registered(rig):
    assert not await rig.admin.is_registered("T1", "C1")
    rig.registry.add(registered_row())
    assert await rig.admin.is_registered("T1", "C1")
    assert not await rig.admin.is_registered("T2", "C1") and not await rig.admin.is_registered("T1", "C2")
