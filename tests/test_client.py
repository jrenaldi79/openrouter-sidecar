import asyncio
import base64
import json

import httpx
import pytest
import respx
from helpers import BoxCredentials

from openrouter_image_mcp.client import (
    GeneratedImage,
    GenerationResult,
    OpenRouterClient,
)
from openrouter_image_mcp.errors import (
    AuthRequiredError,
    BadRequestError,
    CatalogUnavailableError,
    ForbiddenError,
    InsufficientCreditsError,
    ModerationError,
    NoImageError,
    OpenRouterError,
    ProviderError,
    RateLimitError,
)

KEY = "sk-or-test-abc123"
BASE = "https://openrouter.ai/api/v1"
PNG = b"\x89PNG\r\n\x1a\nfake"
PNG_B64 = base64.b64encode(PNG).decode()


@pytest.fixture
def keyed(keys):
    keys.set(KEY)
    return keys


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


@pytest.fixture
async def client(keys, keyed):
    async with OpenRouterClient(timeout_s=30, credentials=keys.credentials()) as c:
        yield c


async def test_images_payload_and_headers(client, mock):
    route = mock.post(f"{BASE}/images").respond(
        200,
        json={
            "data": [{"b64_json": PNG_B64, "media_type": "image/png"}],
            "usage": {"cost": 0.21},
            "id": "gen-1",
            "provider": "OpenAI",
        },
    )
    payload = {"model": "x/y", "prompt": "a cat", "n": 1}
    result = await client.images(payload)

    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert req.headers["HTTP-Referer"] == "https://github.com/skelly-77/openrouter-image-mcp"
    assert req.headers["X-Title"] == "Northwestern AI Images"
    assert json.loads(req.content) == payload
    # The /api/v1 prefix must survive the relative-path join.
    assert str(req.url) == f"{BASE}/images"
    assert isinstance(result, GenerationResult)
    assert result.images == [GeneratedImage(data=PNG, media_type="image/png")]
    assert result.cost_usd == 0.21
    assert result.usage == {"cost": 0.21}
    assert result.generation_id == "gen-1"
    assert result.provider == "OpenAI"


async def test_chat_image_parsing(client, mock):
    data_url = f"data:image/webp;base64,{PNG_B64}"
    route = mock.post(f"{BASE}/chat/completions").respond(
        200,
        json={
            "id": "gen-2",
            "provider": "Google",
            "choices": [
                {
                    "message": {
                        "content": "Here you go",
                        "images": [{"type": "image_url", "image_url": {"url": data_url}}],
                    }
                }
            ],
            "usage": {"cost": 0.04},
        },
    )
    in_url = "data:image/png;base64,AAAA"
    result = await client.chat_image("m/x", "draw", [in_url], "16:9")

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "m/x"
    assert body["modalities"] == ["image", "text"]
    assert body["image_config"] == {"aspect_ratio": "16:9"}
    content = body["messages"][0]["content"]
    assert body["messages"][0]["role"] == "user"
    assert content[0] == {"type": "text", "text": "draw"}
    assert content[1] == {"type": "image_url", "image_url": {"url": in_url}}
    assert result.images == [GeneratedImage(data=PNG, media_type="image/webp")]
    assert result.raw_text == "Here you go"
    assert result.cost_usd == 0.04
    assert result.generation_id == "gen-2"
    assert result.provider == "Google"


async def test_chat_omits_image_config_without_aspect_ratio(client, mock):
    route = mock.post(f"{BASE}/chat/completions").respond(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "images": [{"image_url": {"url": f"data:image/png;base64,{PNG_B64}"}}],
                    }
                }
            ]
        },
    )
    result = await client.chat_image("m/x", "draw", [], None)
    body = json.loads(route.calls.last.request.content)
    assert "image_config" not in body
    assert result.raw_text is None
    assert result.cost_usd is None
    assert result.generation_id is None
    assert result.provider is None


async def test_chat_no_image_raises_noimage(client, mock):
    mock.post(f"{BASE}/chat/completions").respond(
        200, json={"choices": [{"message": {"content": "I can't help with that"}}]}
    )
    with pytest.raises(NoImageError) as exc:
        await client.chat_image("m/x", "draw", [], None)
    assert "I can't help with that" in str(exc.value)
    assert exc.value.text == "I can't help with that"


async def test_401_deletes_key_and_raises(keys, client, mock):
    mock.post(f"{BASE}/images").respond(401, json={"error": {"message": "bad key"}})
    with pytest.raises(AuthRequiredError) as exc:
        await client.images({"prompt": "x"})
    assert keys.get() is None
    assert "link_openrouter" in str(exc.value)
    assert "no longer valid" in exc.value.message


async def test_no_key_raises_auth_required_without_request(keys, mock):
    route = mock.post(f"{BASE}/images").respond(200, json={})
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(AuthRequiredError) as exc:
            await c.images({"prompt": "x"})
    assert str(exc.value) == BoxCredentials.MISSING  # the credential source's own instructions
    assert route.call_count == 0


async def test_402_credits(client, mock):
    mock.post(f"{BASE}/images").respond(402, json={"error": {"message": "no credits"}})
    with pytest.raises(InsufficientCreditsError) as exc:
        await client.images({"prompt": "x"})
    assert "org admin" in str(exc.value)


async def test_403_moderation_metadata(client, mock):
    mock.post(f"{BASE}/images").respond(
        403,
        json={
            "error": {
                "code": 403,
                "message": "flagged",
                "metadata": {
                    "reasons": ["violence", "gore"],
                    "flagged_input": "a bad prompt",
                    "provider_name": "OpenAI",
                    "model_slug": "x",
                },
            }
        },
    )
    with pytest.raises(ModerationError) as exc:
        await client.images({"prompt": "x"})
    err = exc.value
    assert err.reasons == ["violence", "gore"]
    assert err.flagged_input == "a bad prompt"
    assert err.provider == "OpenAI"
    assert err.message == (
        "Blocked by OpenAI moderation: violence, gore. Flagged: 'a bad prompt'. Not charged."
    )


async def test_moderation_without_provider_or_flagged_input(client, mock):
    mock.post(f"{BASE}/images").respond(
        403, json={"error": {"message": "no", "metadata": {"reasons": ["x"]}}}
    )
    with pytest.raises(ModerationError) as exc:
        await client.images({"prompt": "x"})
    assert exc.value.message == "Blocked by the provider moderation: x. Not charged."


@pytest.mark.parametrize(
    "error",
    [
        {"code": "content_policy_violation", "message": "nope"},
        {"code": 400, "message": "Request rejected by Moderation system"},
    ],
)
async def test_content_policy_code_is_moderation(client, mock, error):
    mock.post(f"{BASE}/images").respond(400, json={"error": error})
    with pytest.raises(ModerationError) as exc:
        await client.images({"prompt": "x"})
    assert "Not charged" in str(exc.value)


async def test_plain_400_and_403(client, mock):
    mock.post(f"{BASE}/images").respond(400, json={"error": {"code": 400, "message": "bad size"}})
    with pytest.raises(BadRequestError) as exc:
        await client.images({"prompt": "x"})
    assert str(exc.value) == "bad size"

    mock.post(f"{BASE}/images").respond(403, text="forbidden plain text")
    with pytest.raises(ForbiddenError) as exc:
        await client.images({"prompt": "x"})
    assert str(exc.value) == "forbidden plain text"


async def test_429_retries_twice_honours_retry_after(client, mock, sleeps):
    route = mock.post(f"{BASE}/images")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}, json={"error": {"message": "slow"}}),
        httpx.Response(429, headers={"Retry-After": "1"}, json={"error": {"message": "slow"}}),
        httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]}),
    ]
    result = await client.images({"prompt": "x"})
    assert len(result.images) == 1
    assert result.images[0].media_type is None
    assert sleeps == [1.0, 1.0]


async def test_429_exhausted_raises_ratelimit(client, mock, sleeps):
    mock.post(f"{BASE}/images").respond(429, json={"error": {"message": "slow"}})
    with pytest.raises(RateLimitError) as exc:
        await client.images({"prompt": "x"})
    assert "Rate limited" in str(exc.value)
    assert sleeps == [1.0, 2.0]


async def test_5xx_retries_once(client, mock, sleeps):
    route = mock.post(f"{BASE}/images")
    route.side_effect = [
        httpx.Response(502, json={"error": {"message": "bad gateway"}}),
        httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]}),
    ]
    result = await client.images({"prompt": "x"})
    assert len(result.images) == 1
    assert sleeps == [1.0]


async def test_5xx_twice_raises_provider_error(client, mock, sleeps):
    mock.post(f"{BASE}/images").respond(502, json={"error": {"message": "bad gateway"}})
    with pytest.raises(ProviderError) as exc:
        await client.images({"prompt": "x"})
    assert "not charged" in str(exc.value)
    assert "HTTP 502" in str(exc.value)
    assert "bad gateway" in str(exc.value)


async def test_error_text_never_contains_key(client, mock):
    mock.post(f"{BASE}/images").respond(
        400, json={"error": {"message": f"invalid key {KEY} supplied"}}
    )
    with pytest.raises(BadRequestError) as exc:
        await client.images({"prompt": "x"})
    assert KEY not in str(exc.value)
    assert KEY not in exc.value.message
    assert "sk-or-***" in str(exc.value)


async def test_unauthenticated_401_keeps_the_key(keys, keyed, mock):
    mock.get(f"{BASE}/images/models").respond(401, json={"error": {"message": "no"}})
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(AuthRequiredError, match="rejected the request"):
            await c.image_models()
    assert keys.get() == KEY


async def test_catalog_network_error_maps(keys, mock):
    mock.get(f"{BASE}/images/models").mock(side_effect=httpx.ConnectError("boom"))
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(CatalogUnavailableError) as exc:
            await c.image_models()
    assert "Couldn't reach the OpenRouter catalog" in str(exc.value)


async def test_catalog_methods_unauthenticated(keys, keyed, mock):
    r1 = mock.get(f"{BASE}/images/models").respond(200, json={"data": [{"id": "a/b"}]})
    r2 = mock.get(f"{BASE}/models", params={"output_modalities": "image"}).respond(
        200, json={"data": [{"id": "c/d"}]}
    )
    r3 = mock.get(f"{BASE}/images/models/a/b/endpoints").respond(
        200, json={"id": "a/b", "endpoints": [{"provider_name": "P"}]}
    )
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        assert await c.image_models() == [{"id": "a/b"}]
        assert await c.image_models_meta() == [{"id": "c/d"}]
        assert await c.image_model_endpoints("a/b") == [{"provider_name": "P"}]
    for r in (r1, r2, r3):
        assert "Authorization" not in r.calls.last.request.headers


async def test_catalog_timeout_maps_to_catalog_unavailable(keys, mock):
    mock.get(f"{BASE}/images/models").mock(side_effect=httpx.ReadTimeout("slow"))
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(CatalogUnavailableError) as exc:
            await c.image_models()
    assert "Couldn't reach the OpenRouter catalog" in str(exc.value)
    assert "Not charged" not in str(exc.value)


async def test_catalog_5xx_maps_to_catalog_unavailable(keys, mock, sleeps):
    route = mock.get(f"{BASE}/images/models").respond(500, text="oops")
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(CatalogUnavailableError) as exc:
            await c.image_models()
    assert "HTTP 500" in str(exc.value)
    assert "charged" not in str(exc.value)
    assert route.call_count == 2
    assert sleeps == [1.0]


async def test_catalog_429_maps_to_catalog_unavailable(keys, mock, sleeps):
    mock.get(f"{BASE}/images/models").respond(429, json={"error": {"message": "slow"}})
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(CatalogUnavailableError) as exc:
            await c.image_models()
    assert "HTTP 429" in str(exc.value)
    assert sleeps == [1.0, 2.0]


async def test_catalog_non_json_maps_to_catalog_unavailable(keys, mock):
    mock.get(f"{BASE}/images/models").respond(200, text="<html>oops</html>")
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        with pytest.raises(CatalogUnavailableError):
            await c.image_models()


async def test_as_list_dict_without_endpoints_is_empty(keys, mock):
    mock.get(f"{BASE}/images/models/a/b/endpoints").respond(200, json={"id": "a/b"})
    async with OpenRouterClient(timeout_s=5, credentials=keys.credentials()) as c:
        assert await c.image_model_endpoints("a/b") == []


@pytest.mark.parametrize(
    ("header", "expected"), [("999", 30.0), ("inf", 1.0), ("nan", 1.0), ("soon", 1.0), ("-5", 0.0)]
)
async def test_retry_after_is_clamped_and_sanitised(client, mock, sleeps, header, expected):
    route = mock.post(f"{BASE}/images")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": header}, json={}),
        httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]}),
    ]
    await client.images({"prompt": "x"})
    assert sleeps == [expected]


async def test_chat_rejects_non_data_url(client, mock):
    mock.post(f"{BASE}/chat/completions").respond(
        200,
        json={
            "choices": [
                {"message": {"images": [{"image_url": {"url": "https://example.com/a.png"}}]}}
            ]
        },
    )
    with pytest.raises(ProviderError):
        await client.chat_image("m/x", "draw", [], None)


@pytest.mark.parametrize("bad", ["!!!not base64!!!", "A"])
async def test_bad_base64_raises_provider_error(client, mock, bad):
    mock.post(f"{BASE}/images").respond(200, json={"data": [{"b64_json": bad}]})
    with pytest.raises(ProviderError):
        await client.images({"prompt": "x"})

    mock.post(f"{BASE}/chat/completions").respond(
        200, json={"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{bad}"}}]}}]}
    )
    with pytest.raises(ProviderError):
        await client.chat_image("m/x", "draw", [], None)


async def test_chat_empty_payload_raises_provider_error(client, mock):
    mock.post(f"{BASE}/chat/completions").respond(
        200,
        json={"choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64,"}}]}}]},
    )
    with pytest.raises(ProviderError):
        await client.chat_image("m/x", "draw", [], None)


async def test_key_info_and_generation(client, mock):
    mock.get(f"{BASE}/key").respond(200, json={"data": {"usage": 1.5, "limit": None}})
    route = mock.get(f"{BASE}/generation").respond(200, json={"data": {"id": "gen-9"}})
    assert await client.key_info() == {"usage": 1.5, "limit": None}
    assert await client.generation("gen-9") == {"id": "gen-9"}
    assert route.calls.last.request.url.params["id"] == "gen-9"
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"


async def test_timeout_maps_to_provider_error(client, mock):
    mock.post(f"{BASE}/images").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError) as exc:
        await client.images({"prompt": "x"})
    assert str(exc.value) == "OpenRouter request timed out after 30s. Not charged."


async def test_transport_error_maps_to_provider_error(client, mock):
    mock.post(f"{BASE}/images").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderError) as exc:
        await client.images({"prompt": "x"})
    assert "Couldn't reach OpenRouter" in str(exc.value)
    assert "refused" in str(exc.value)


async def test_images_empty_data_raises_noimage(client, mock):
    mock.post(f"{BASE}/images").respond(200, json={"data": []})
    with pytest.raises(NoImageError):
        await client.images({"prompt": "x"})


async def test_custom_transport_is_used(keys, keyed):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE}/key"
        return httpx.Response(200, json={"data": {"ok": True}})

    async with OpenRouterClient(timeout_s=5, transport=httpx.MockTransport(handler),
                                credentials=keys.credentials()) as c:
        assert await c.key_info() == {"ok": True}


def test_error_hierarchy():
    for cls in (
        AuthRequiredError,
        InsufficientCreditsError,
        ForbiddenError,
        BadRequestError,
        RateLimitError,
        ProviderError,
        CatalogUnavailableError,
        ModerationError,
        NoImageError,
    ):
        assert issubclass(cls, OpenRouterError)


async def test_5xx_html_body_truncated_in_message(client, mock, sleeps):
    html = "<html><body>" + "x" * 10_000 + "</body></html>"
    mock.post(f"{BASE}/images").respond(502, text=html)
    with pytest.raises(ProviderError) as exc:
        await client.images({"prompt": "x"})
    message = exc.value.message
    assert "HTTP 502" in message and "<html><body>xxx" in message
    assert "x" * 400 not in message
    assert "…" in message
    assert len(message) < 500


async def test_4xx_text_body_truncated_in_message(client, mock):
    mock.post(f"{BASE}/images").respond(400, text="y" * 5_000)
    with pytest.raises(BadRequestError) as exc:
        await client.images({"prompt": "x"})
    assert exc.value.message == "y" * 300 + "…"
