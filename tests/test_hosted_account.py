"""account_status reporting: key name, spend and budget; a rejected key is rechecked once."""

import base64
import os

import httpx
import pytest
import respx

from openrouter_image_mcp.hosted.account import key_report, usage_lines
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.provisioning import DISABLED, KeyProvisioner
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from tests import jwt_helpers as h
from tests.test_hosted_provisioning import FakeManager

WS = "ws-2027"
ALICE = Principal(h.ISSUER, "email|alice", WS, "2027", "alice@u.edu")
KEY_URL = "https://openrouter.ai/api/v1/key"


@pytest.fixture
def provisioner(tmp_path):
    store = StudentKeyStore(tmp_path / "db.sqlite3", Vault("v1:" + base64.b64encode(os.urandom(32)).decode()))
    return KeyProvisioner(store, FakeManager(), {WS})


def test_usage_lines_prefer_the_effective_budget():
    lines = usage_lines({"usage_monthly": 3, "effective_budget": {
        "limit_usd": 25, "spend_usd": 3, "reset_interval": "monthly", "resets_at": "2026-11-01T00:00:00Z"}})
    assert lines == ["Spent this month: $3.00.",
                     "Budget: $3.00 of $25.00 used (monthly, resets 2026-11-01; set by your class workspace)."]
    assert usage_lines({"usage_monthly": 0, "limit": 10, "limit_remaining": 7})[1] == "Key limit: $10.00, $7.00 left."
    assert usage_lines({}) == ["Spent this month: ?."]


@respx.mock
async def test_report_provisions_and_never_shows_the_key(provisioner):
    route = respx.get(KEY_URL).mock(return_value=httpx.Response(200, json={"data": {"usage_monthly": 1}}))
    lines = await key_report(provisioner, ALICE)
    assert lines[0].startswith("OpenRouter key: Sidecar · alice@u.edu · 2027 (issued ")
    assert "sk-or-v1" not in "\n".join(lines)
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-or-v1-new1"


@respx.mock
async def test_report_replaces_a_key_deleted_by_an_admin(provisioner):
    await provisioner.ensure(ALICE)
    provisioner.manager.keys.pop("h1")
    respx.get(KEY_URL).mock(side_effect=lambda request: httpx.Response(
        401 if request.headers["Authorization"].endswith("new1") else 200, json={"data": {}}))
    lines = await key_report(provisioner, ALICE)
    assert lines[0].startswith("OpenRouter key: Sidecar")
    assert provisioner.store.row(ALICE.key, WS).key_hash == "h2"


@respx.mock
async def test_report_explains_a_disabled_key(provisioner):
    await provisioner.ensure(ALICE)
    provisioner.manager.keys["h1"]["disabled"] = True
    respx.get(KEY_URL).mock(return_value=httpx.Response(401, json={}))
    assert await key_report(provisioner, ALICE) == [f"OpenRouter key: not available. {DISABLED}"]
