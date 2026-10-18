import httpx
import pytest
import respx

from openrouter_image_mcp import keystore
from openrouter_image_mcp.advisor_store import AdvisorAccessError, Scope
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.credentials import KeyringCredentials
from openrouter_image_mcp.errors import AuthRequiredError


def test_keyring_source_matches_legacy_scope(memory_keyring):
    keystore.set_key("sk-test", workspace_id="ws")
    key, scope = KeyringCredentials("ws").resolve()
    assert key == "sk-test"
    assert scope.workspace == "ws" and len(scope.owner) == 64  # sha256 hex, as before


def test_keyring_source_requires_sign_in(memory_keyring):
    with pytest.raises(AuthRequiredError):
        KeyringCredentials("ws").resolve()


def test_check_detects_credential_change(memory_keyring):
    keystore.set_key("sk-a", workspace_id="ws")
    source = KeyringCredentials("ws")
    _, scope = source.resolve()
    source.check(scope)
    keystore.set_key("sk-b", workspace_id="ws")
    with pytest.raises(AdvisorAccessError):
        source.check(scope)


def test_check_rejects_foreign_workspace(memory_keyring):
    keystore.set_key("sk-a", workspace_id="ws")
    source = KeyringCredentials("ws")
    _, scope = source.resolve()
    with pytest.raises(AdvisorAccessError):
        source.check(Scope(scope.owner, "other"))


def test_forget_deletes_local_key(memory_keyring):
    keystore.set_key("sk-a", workspace_id="ws")
    KeyringCredentials("ws").forget()
    assert keystore.get_key(workspace_id="ws") is None


class FixedCredentials:
    workspace_id = "ws"

    def __init__(self):
        self.forgotten = False

    def resolve(self):
        return "sk-injected", Scope("owner", "ws")

    def check(self, scope):
        pass

    def forget(self):
        self.forgotten = True


@respx.mock
async def test_client_uses_injected_credentials():
    route = respx.get("https://openrouter.ai/api/v1/key").mock(
        return_value=httpx.Response(200, json={"data": {"label": "x"}}))
    source = FixedCredentials()
    async with OpenRouterClient(timeout_s=5, workspace_id="ws", credentials=source) as client:
        await client.key_info()
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-injected"


@respx.mock
async def test_client_401_forgets_injected_credentials():
    respx.get("https://openrouter.ai/api/v1/key").mock(return_value=httpx.Response(401, json={}))
    source = FixedCredentials()
    async with OpenRouterClient(timeout_s=5, workspace_id="ws", credentials=source) as client:
        with pytest.raises(AuthRequiredError):
            await client.key_info()
    assert source.forgotten
