"""Northwestern cohort selection and credential isolation regressions."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp import Client

from openrouter_image_mcp import cli, keystore
from openrouter_image_mcp.auth import LoginManager
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import load_settings
from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.server import build_server

WORKSPACES = {
    "2027": "21082e84-ae02-4639-ad40-c7251b98ab10",
    "2028": "8804f9c5-afd2-4de3-8d9f-f74b3d58c651",
}


@pytest.mark.parametrize("cohort", WORKSPACES)
def test_cohort_selects_workspace_and_output(cohort):
    settings = load_settings({"OPENROUTER_IMAGE_COHORT": cohort})
    assert settings.workspace_id == WORKSPACES[cohort]
    assert settings.cohort == cohort
    assert (
        settings.output_dir
        == Path.home() / "Pictures" / "Northwestern AI" / f"Class of {cohort}"
    )


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"OPENROUTER_IMAGE_COHORT": "2029"},
        {"OPENROUTER_IMAGE_WORKSPACE_ID": ""},
        {"OPENROUTER_IMAGE_WORKSPACE_ID": "old-firm"},
        {
            "OPENROUTER_IMAGE_COHORT": "2027",
            "OPENROUTER_IMAGE_WORKSPACE_ID": WORKSPACES["2028"],
        },
    ],
)
def test_invalid_or_ambiguous_selection_is_rejected(env):
    with pytest.raises(ValueError, match="cohort|workspace|COHORT|WORKSPACE"):
        load_settings(env)


def test_explicit_known_workspace_resolves_cohort():
    settings = load_settings({"OPENROUTER_IMAGE_WORKSPACE_ID": WORKSPACES["2028"]})
    assert settings.cohort == "2028"


def test_credentials_are_isolated_from_each_other_and_legacy(memory_keyring):
    memory_keyring.set_password("openrouter-image-mcp", "default", "legacy-key")
    keystore.set_key("key-2027", workspace_id=WORKSPACES["2027"])
    keystore.set_key("key-2028", workspace_id=WORKSPACES["2028"])
    keystore.delete_key(workspace_id=WORKSPACES["2027"])
    assert keystore.get_key(workspace_id=WORKSPACES["2027"]) is None
    assert keystore.get_key(workspace_id=WORKSPACES["2028"]) == "key-2028"
    assert (
        memory_keyring.get_password("openrouter-image-mcp", "default") == "legacy-key"
    )


@pytest.mark.asyncio
async def test_unauthorized_request_removes_only_its_cohort_key(memory_keyring):
    keystore.set_key("key-2027", workspace_id=WORKSPACES["2027"])
    keystore.set_key("key-2028", workspace_id=WORKSPACES["2028"])
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(401, json={"error": {"message": "revoked"}})

    async with OpenRouterClient(
        timeout_s=5,
        workspace_id=WORKSPACES["2028"],
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AuthRequiredError):
            await client.key_info()
    assert seen == ["Bearer key-2028"]
    assert keystore.get_key(workspace_id=WORKSPACES["2027"]) == "key-2027"
    assert keystore.get_key(workspace_id=WORKSPACES["2028"]) is None


@pytest.mark.asyncio
async def test_login_does_not_reuse_other_cohort_key(memory_keyring):
    keystore.set_key("key-2027", workspace_id=WORKSPACES["2027"])
    settings = load_settings({"OPENROUTER_IMAGE_COHORT": "2028"})
    async with OpenRouterClient(
        timeout_s=5, workspace_id=settings.workspace_id
    ) as client:
        manager = LoginManager(client, settings, open_browser=lambda url: None)
        try:
            result = await manager.start()
            assert result["state"] == "pending"
            params = parse_qs(urlparse(result["url"]).query)
            assert params["required_workspace_id"] == [WORKSPACES["2028"]]
            assert "2028" in params["key_label"][0]
        finally:
            manager.cancel()


@pytest.mark.parametrize("cohort", WORKSPACES)
def test_desktop_config_runs_one_local_server_for_selected_cohort(cohort):
    project = Path(r"C:\work\northwestern")
    executable = Path(r"C:\work\northwestern\.venv\Scripts\python.exe")
    config = json.loads(cli.render_local_config(project, executable, cohort=cohort))
    assert set(config["mcpServers"]) == {"openrouter-sidecar"}
    entry = config["mcpServers"]["openrouter-sidecar"]
    assert entry["command"] == str(executable)
    assert entry["args"] == ["-m", "openrouter_image_mcp.cli"]
    assert entry["env"]["OPENROUTER_IMAGE_COHORT"] == cohort
    assert entry["env"]["OPENROUTER_IMAGE_WORKSPACE_ID"] == WORKSPACES[cohort]
    assert entry["env"]["PYTHONPATH"] == str(project / "src")
    assert "git+https" not in json.dumps(entry)


@pytest.mark.asyncio
async def test_two_mcp_profiles_status_logout_and_revocation(memory_keyring):
    for cohort, workspace in WORKSPACES.items():
        keystore.set_key(f"key-{cohort}", workspace_id=workspace)
    revoked = False
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        if revoked:
            return httpx.Response(401, json={"error": {"message": "revoked"}})
        return httpx.Response(200, json={"data": {"label": "test key", "usage": 0}})

    settings = load_settings({"OPENROUTER_IMAGE_COHORT": "2028"})
    async with (
        OpenRouterClient(
            timeout_s=5,
            workspace_id=settings.workspace_id,
            transport=httpx.MockTransport(handler),
        ) as http,
        Client(build_server(settings, http), raise_exceptions=False) as session,
    ):
        tools = await session.list_tools()
        assert len(tools.tools) == 24
        status = await session.call_tool("account_status", {})
        text = "\n".join(item.text for item in status.content if hasattr(item, "text"))
        assert "Northwestern University / Class of 2028" in text
        assert WORKSPACES["2028"] in text
        assert seen == ["Bearer key-2028"]
        await session.call_tool("auth_logout", {})
        assert keystore.get_key(workspace_id=WORKSPACES["2028"]) is None
        assert keystore.get_key(workspace_id=WORKSPACES["2027"]) == "key-2027"
        status = await session.call_tool("account_status", {})
        assert "Class of 2028" in status.content[0].text
        assert "Not signed in" in status.content[0].text
        keystore.set_key("key-2028", workspace_id=WORKSPACES["2028"])
        revoked = True
        status = await session.call_tool("account_status", {})
        assert status.is_error
        assert "Class of 2028" in status.content[0].text
        assert keystore.get_key(workspace_id=WORKSPACES["2027"]) == "key-2027"
        assert keystore.get_key(workspace_id=WORKSPACES["2028"]) is None


@pytest.mark.asyncio
async def test_mcp_rejects_a_client_with_another_workspace(memory_keyring):
    settings = load_settings({"OPENROUTER_IMAGE_COHORT": "2028"})
    async with OpenRouterClient(timeout_s=5, workspace_id=WORKSPACES["2027"]) as http:
        with pytest.raises(ValueError, match="workspace"):
            build_server(settings, http)


def test_cli_logout_uses_selected_cohort(memory_keyring, monkeypatch, capsys):
    for cohort, workspace in WORKSPACES.items():
        keystore.set_key(f"key-{cohort}", workspace_id=workspace)
    monkeypatch.setenv("OPENROUTER_IMAGE_COHORT", "2028")
    monkeypatch.delenv("OPENROUTER_IMAGE_WORKSPACE_ID", raising=False)
    assert cli.main(["logout"]) == 0
    assert "Class of 2028" in capsys.readouterr().out
    assert keystore.get_key(workspace_id=WORKSPACES["2027"]) == "key-2027"
    assert keystore.get_key(workspace_id=WORKSPACES["2028"]) is None
