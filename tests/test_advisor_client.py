"""Container file download: auth, id validation and the size cap."""

import httpx
import pytest

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.errors import (
    AuthRequiredError,
    BadRequestError,
    ProviderError,
)

KEY = "sk-or-v1-alice"


def make(handler):
    return AdvisorClient("ws-1", 30, transport=httpx.MockTransport(handler))


async def test_container_file_returns_bytes_with_auth_and_workspace():
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, content=b"hello")

    client = make(handle)
    assert await client.container_file(KEY, "cntr_1", "file-2", max_bytes=100) == b"hello"
    request = seen[0]
    assert request.url.path.endswith("/containers/cntr_1/files/file-2/content")
    assert request.url.params["workspace_id"] == "ws-1"
    assert request.headers["authorization"] == "Bearer " + KEY
    await client.aclose()


async def test_container_file_size_cap_on_body():
    client = make(lambda request: httpx.Response(200, content=b"x" * 11))
    with pytest.raises(ProviderError):
        await client.container_file(KEY, "c", "f", max_bytes=10)
    assert await client.container_file(KEY, "c", "f", max_bytes=11) == b"x" * 11
    await client.aclose()


async def test_container_file_content_length_over_cap_rejected():
    def handle(request):
        return httpx.Response(200, headers={"content-length": "5000"}, content=b"x" * 5000)

    client = make(handle)
    with pytest.raises(ProviderError):
        await client.container_file(KEY, "c", "f", max_bytes=100)
    await client.aclose()


async def test_container_file_non_2xx_is_provider_error():
    client = make(lambda request: httpx.Response(404))
    with pytest.raises(ProviderError):
        await client.container_file(KEY, "c", "f", max_bytes=10)
    await client.aclose()


async def test_container_file_401_is_auth_required():
    client = make(lambda request: httpx.Response(401))
    with pytest.raises(AuthRequiredError):
        await client.container_file(KEY, "c", "f", max_bytes=10)
    await client.aclose()


@pytest.mark.parametrize("container,file", [
    ("../x", "f"), ("c", "a/b"), ("c", ""), ("", "f"), ("c", "f?x=1"), ("c" * 129, "f"),
    ("c", "f\n"), ("c", "..%2f"),
])
async def test_container_file_rejects_bad_ids(container, file):
    called = []

    def handle(request):
        called.append(request)
        return httpx.Response(200, content=b"")

    client = make(handle)
    with pytest.raises(BadRequestError):
        await client.container_file(KEY, container, file, max_bytes=10)
    assert not called
    await client.aclose()
