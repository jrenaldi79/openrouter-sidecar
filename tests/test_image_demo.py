"""Image walkthrough calls use a pinned credential and never retry inference."""

import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from openrouter_image_mcp import keystore
from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.config import COHORT_WORKSPACES, Settings
from openrouter_image_mcp.errors import OpenRouterError
from openrouter_image_mcp.image_onboarding import (
    IMAGE_PROMPT,
    PinnedDemoClient,
    image_runner,
)


async def test_image_demo_failure_is_one_request_and_redacts_provider_body():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(500, text="secret-test-key provider body")

    client = PinnedDemoClient("secret-test-key", "workspace", 30, lambda: None,
                              transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OpenRouterError, match="no automatic retry") as failure:
            await client.images({"model": "test/image", "prompt": "Synthetic demo", "n": 1})
        assert len(requests) == 1
        assert requests[0].headers["authorization"] == "Bearer secret-test-key"
        assert "secret-test-key" not in str(failure.value)
        assert "secret-test-key" not in requests[0].content.decode()
    finally:
        await client.aclose()


async def test_demo_credential_change_during_response_withholds_data():
    changed = False
    calls = 0

    def guard():
        if changed:
            raise OpenRouterError("Sign-in changed; data withheld")

    async def handle(request):
        nonlocal changed, calls
        calls += 1
        changed = True
        return httpx.Response(200, json={"data": []})

    client = PinnedDemoClient("secret-test-key", "workspace", 30, guard,
                              transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OpenRouterError, match="Sign-in changed"):
            await client.images({"model": "test/image", "prompt": "Synthetic demo", "n": 1})
        with pytest.raises(OpenRouterError, match="Sign-in changed"):
            await client.key_info()
        assert calls == 1
    finally:
        await client.aclose()


async def test_demo_timeout_is_ambiguous_and_not_retried():
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret-test-key", request=request)

    client = PinnedDemoClient("secret-test-key", "workspace", 30, lambda: None,
                              transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OpenRouterError, match="may have been billed") as failure:
            await client.images({"model": "test/image", "prompt": "Synthetic demo", "n": 1})
        assert calls == 1
        assert "secret-test-key" not in str(failure.value)
    finally:
        await client.aclose()


@pytest.mark.parametrize("parameters", [{}, {"output_format": {"values": ["SVG", "PNG"]}}])
async def test_image_runner_fixed_prompt_single_image_and_scoped_local_preview(tmp_path, memory_keyring, parameters):
    workspace = COHORT_WORKSPACES["2027"]
    keystore.set_key("synthetic-demo-key", workspace_id=workspace)
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "purple").save(buffer, format="PNG")
    image = base64.b64encode(buffer.getvalue()).decode()
    seen = []

    async def handle(request):
        seen.append(request)
        if request.url.path.endswith("/images/models"):
            return httpx.Response(200, json={"data": [{"id": "openai/demo-image", "name": "Demo Image",
                                                       "supported_parameters": parameters}]})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/images"):
            return httpx.Response(200, json={"data": [{"b64_json": image, "media_type": "image/png"}],
                                           "usage": {"cost": 0.04}})
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"usage": 0.04}})
        pytest.fail("Unexpected image demo HTTP operation")

    transport = httpx.MockTransport(handle)
    settings = Settings(workspace, tmp_path / "unscoped", 2048, 10, 0.03)
    client = AdvisorClient(workspace, 10, transport=transport)
    advisors = AdvisorService(workspace, AdvisorStore(tmp_path / "advisor.sqlite3"), client)
    guards = []

    def guard():
        guards.append("scope")

    guard.before_request = lambda: guards.append("revision")
    try:
        result = await image_runner(settings, advisors, transport=transport)("openai/demo-image", guard)
        paid = [request for request in seen if request.method == "POST"]
        assert len(paid) == 1
        expected = {"model": "openai/demo-image", "prompt": IMAGE_PROMPT, "n": 1}
        if parameters:
            expected["output_format"] = "PNG"
        assert json.loads(paid[0].content) == expected
        assert paid[0].headers["authorization"] == "Bearer synthetic-demo-key"
        assert "revision" in guards
        assert len(result["images"]) == 1
        preview = result["images"][0]
        assert "dataUri" not in preview  # same split as the image tools: bytes only in _meta
        assert [p[:23] for p in result["_meta"]["sidecarPreviews"]] == ["data:image/jpeg;base64,"]
        assert "sidecar-demo-images" in preview["path"]
        assert "synthetic-demo-key" not in json.dumps(result)
        assert not settings.output_dir.exists()
    finally:
        await client.aclose()


@pytest.mark.parametrize("parameters", [
    {"output_format": {"values": ["SVG"]}},
    {"input_references": {"min": 1, "max": 1}},
    {"input_references": {"min": "1", "max": 1}},
    {"input_references": {"min": True, "max": 1}},
    {"input_references": {"min": None, "max": 1}},
    {"input_references": {"min": -1, "max": 1}},
])
async def test_unusable_demo_model_is_hidden_and_rejected_before_paid_post(tmp_path, memory_keyring, parameters):
    from openrouter_image_mcp.catalog import Catalog
    from openrouter_image_mcp.preference_tools import image_choices

    workspace = COHORT_WORKSPACES["2027"]
    keystore.set_key("synthetic-demo-key", workspace_id=workspace)
    seen = []

    async def handle(request):
        seen.append(request)
        assert request.method == "GET", "Unusable demo models must never run paid generation"
        if request.url.path.endswith("/images/models"):
            return httpx.Response(200, json={"data": [{"id": "openai/demo-image", "name": "Demo Image",
                                                       "supported_parameters": parameters}]})
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handle)
    client = PinnedDemoClient("synthetic-demo-key", workspace, 10, lambda: None, transport=transport)
    advisor_client = AdvisorClient(workspace, 10, transport=transport)
    advisors = AdvisorService(workspace, AdvisorStore(tmp_path / "advisor.sqlite3"), advisor_client)
    try:
        models = await Catalog(client).all()
        _, scope = advisors._credentials()
        assert image_choices(models, "synthetic-demo-key", scope) == []
        settings = Settings(workspace, tmp_path / "out", 2048, 10, 0.03)
        with pytest.raises(OpenRouterError, match="demo model"):
            await image_runner(settings, advisors, transport=transport)("openai/demo-image", lambda: None)
        assert not any(request.method == "POST" for request in seen)
    finally:
        await client.aclose()
        await advisor_client.aclose()
