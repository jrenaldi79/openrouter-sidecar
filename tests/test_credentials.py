"""Every OpenRouter caller names where its key comes from; there is no keyring default."""

import httpx
import pytest
import respx

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.errors import AuthRequiredError

WS = COHORT_WORKSPACES["2027"]


class FixedCredentials:
    workspace_id = WS

    def __init__(self):
        self.forgotten = False

    def resolve(self):
        return "sk-injected", Scope("owner", WS)

    def check(self, scope):
        pass

    def forget(self):
        self.forgotten = True


def test_client_requires_a_credential_source():
    with pytest.raises(TypeError):
        OpenRouterClient(timeout_s=5)


async def test_client_workspace_comes_from_its_credentials():
    async with OpenRouterClient(timeout_s=5, credentials=FixedCredentials()) as client:
        assert client.workspace_id == WS


async def test_advisor_service_requires_a_credential_source(tmp_path):
    advisor_client = AdvisorClient(WS, 30)
    try:
        with pytest.raises(TypeError):
            AdvisorService(WS, AdvisorStore(tmp_path / "advisor.sqlite3"), advisor_client)
    finally:
        await advisor_client.aclose()


@respx.mock
async def test_client_uses_injected_credentials():
    route = respx.get("https://openrouter.ai/api/v1/key").mock(
        return_value=httpx.Response(200, json={"data": {"label": "x"}}))
    source = FixedCredentials()
    async with OpenRouterClient(timeout_s=5, workspace_id=WS, credentials=source) as client:
        await client.key_info()
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-injected"


@respx.mock
async def test_client_401_forgets_injected_credentials():
    respx.get("https://openrouter.ai/api/v1/key").mock(return_value=httpx.Response(401, json={}))
    source = FixedCredentials()
    async with OpenRouterClient(timeout_s=5, workspace_id=WS, credentials=source) as client:
        with pytest.raises(AuthRequiredError):
            await client.key_info()
    assert source.forgotten


@respx.mock
async def test_missing_key_message_comes_from_the_source(keys):
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as client:
        with pytest.raises(AuthRequiredError, match="No OpenRouter key"):
            await client.key_info()
    assert not respx.calls
