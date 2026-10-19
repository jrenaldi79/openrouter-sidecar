import json
from pathlib import Path

import httpx
import pytest
import respx

from openrouter_image_mcp.catalog import (
    Catalog,
    ModelCapabilities,
    price_key,
    validate_params,
)
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.errors import (
    BadRequestError,
    CatalogUnavailableError,
    OpenRouterError,
    UnknownModelError,
)

BASE = "https://openrouter.ai/api/v1"
FIXTURES = Path(__file__).parent / "fixtures"
SUNBURST = "openai/gpt-image-2.5-sunburst"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def client(keys):
    async with OpenRouterClient(timeout_s=30, credentials=keys.credentials()) as c:
        yield c


@pytest.fixture
def routes(mock):
    return (
        mock.get(f"{BASE}/images/models").respond(
            200, json=fixture("images_models.json")
        ),
        mock.get(f"{BASE}/models", params={"output_modalities": "image"}).respond(
            200, json=fixture("models_image.json")
        ),
    )


@pytest.fixture
def catalog(client, routes, clock):
    return Catalog(client, ttl_s=600, stale_ok_s=86400, clock=clock)


def caps(**over):
    base = {
        "id": "x/y",
        "name": "X Y",
        "description": "",
        "created": 1,
        "route": "images",
        "accepts_images": True,
        "max_input_images": 4,
        "aspect_ratios": ["1:1", "16:9"],
        "resolutions": ["1K", "2K"],
        "qualities": ["low", "high"],
        "backgrounds": ["auto", "opaque"],
        "output_formats": ["png", "jpeg"],
        "max_n": 1,
        "seed": True,
        "size": True,
        "is_moderated": None,
        "pricing": {},
    }
    base.update(over)
    return ModelCapabilities(**base)


def params(**over):
    base = {
        "n": 1,
        "aspect_ratio": None,
        "resolution": None,
        "quality": None,
        "background": None,
        "output_format": None,
        "seed": None,
        "size": None,
        "input_count": 0,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ capabilities


async def test_sunburst_caps(catalog):
    m = await catalog.get(SUNBURST)
    assert "21:9" in m.aspect_ratios
    assert m.qualities[-1] == "max"
    assert m.max_n == 10
    assert m.max_input_images == 16
    assert m.accepts_images
    assert m.route == "images"
    assert m.is_moderated is True
    assert m.pricing["image_output"] == "0.00003"
    assert m.name == "OpenAI: GPT Image 2.5 Sunburst"
    assert m.created == 1788916368
    assert m.backgrounds == ["auto", "transparent", "opaque"]
    assert m.resolutions is None
    assert m.output_formats is None
    assert m.seed is False
    assert m.size is False


async def test_chat_only_models_routed_to_chat(catalog):
    m = await catalog.get("openrouter/auto")
    assert m.route == "chat"
    assert m.aspect_ratios is None
    assert m.resolutions is None
    assert m.qualities is None
    assert m.backgrounds is None
    assert m.output_formats is None
    assert m.max_n == 1
    assert m.seed is False
    assert m.size is False
    assert m.accepts_images is True
    assert m.max_input_images == 16
    assert m.is_moderated is False


async def test_chat_route_input_images_from_meta(client, mock, clock):
    meta = [
        {
            "id": "chat/vision",
            "name": "Vision",
            "description": "d",
            "created": 5,
            "architecture": {"input_modalities": ["text", "image"]},
            "pricing": {"completion": "0.5"},
            "top_provider": {"is_moderated": False},
        },
        {"id": "chat/textonly", "architecture": {"input_modalities": ["text"]}},
    ]
    mock.get(f"{BASE}/images/models").respond(200, json={"data": []})
    mock.get(f"{BASE}/models").respond(200, json={"data": meta})
    cat = Catalog(client, clock=clock)
    vision = await cat.get("chat/vision")
    assert vision.accepts_images and vision.max_input_images == 16
    assert vision.is_moderated is False
    assert vision.pricing == {"completion": "0.5"}
    textonly = await cat.get("chat/textonly")
    assert not textonly.accepts_images and textonly.max_input_images == 0
    assert textonly.pricing == {} and textonly.is_moderated is None


async def test_missing_params_mean_unsupported(client, mock, clock):
    images = [
        {
            "id": "bare/model",
            "name": "Bare",
            "description": "",
            "created": 7,
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["image"],
            },
            "supported_parameters": {},
        },
        {
            "id": "full/model",
            "name": "Full",
            "created": 9,
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": {
                "aspect_ratio": {"type": "enum", "values": ["1:1"]},
                "resolution": {"type": "enum", "values": ["1K"]},
                "output_format": {"type": "enum", "values": ["png"]},
                "seed": {"type": "boolean"},
                "size": {"type": "enum", "values": ["1024x1024"]},
            },
        },
    ]
    mock.get(f"{BASE}/images/models").respond(200, json={"data": images})
    mock.get(f"{BASE}/models").respond(200, json={"data": []})
    cat = Catalog(client, clock=clock)
    bare = await cat.get("bare/model")
    assert bare.aspect_ratios is None
    assert bare.resolutions is None
    assert bare.qualities is None
    assert bare.backgrounds is None
    assert bare.output_formats is None
    assert bare.max_n == 1
    assert bare.max_input_images == 0
    assert bare.accepts_images is False
    assert bare.seed is False
    assert bare.size is False
    assert bare.pricing == {} and bare.is_moderated is None
    full = await cat.get("full/model")
    assert full.aspect_ratios == ["1:1"]
    assert full.resolutions == ["1K"]
    assert full.output_formats == ["png"]
    assert full.seed is True
    assert full.size is True


async def test_all_sorted_newest_first(client, mock, clock):
    images = [
        {"id": "a/old", "created": 1},
        {"id": "a/new", "created": 30},
        {"id": "a/mid", "created": 20},
    ]
    mock.get(f"{BASE}/images/models").respond(200, json={"data": images})
    mock.get(f"{BASE}/models").respond(
        200, json={"data": [{"id": "chat/newest", "created": 99}]}
    )
    cat = Catalog(client, clock=clock)
    assert [m.id for m in await cat.all()] == ["chat/newest", "a/new", "a/mid", "a/old"]


async def test_name_description_fall_back_to_meta(client, mock, clock):
    mock.get(f"{BASE}/images/models").respond(200, json={"data": [{"id": "a/b"}]})
    mock.get(f"{BASE}/models").respond(
        200,
        json={
            "data": [
                {"id": "a/b", "name": "From Meta", "description": "md", "created": 4}
            ]
        },
    )
    m = await Catalog(client, clock=clock).get("a/b")
    assert (m.name, m.description, m.created) == ("From Meta", "md", 4)
    assert m.route == "images"


# ----------------------------------------------------------------------- lookup


async def test_unknown_model_suggestions(catalog):
    with pytest.raises(UnknownModelError) as exc:
        await catalog.get("openai/gpt-image-2.5-sunbrust")
    assert SUNBURST in exc.value.suggestions
    assert exc.value.model_id == "openai/gpt-image-2.5-sunbrust"
    assert isinstance(exc.value, OpenRouterError)
    assert "Unknown image model 'openai/gpt-image-2.5-sunbrust'." in str(exc.value)
    assert "Did you mean:" in str(exc.value)
    assert "list_image_models" in str(exc.value)


def test_unknown_model_without_suggestions():
    err = UnknownModelError("zzz", [])
    assert (
        str(err)
        == "Unknown image model 'zzz'. Call `list_image_models` to see current models."
    )


async def test_endpoints_cached_and_verified(catalog, mock):
    route = mock.get(f"{BASE}/images/models/{SUNBURST}/endpoints").respond(
        200, json=fixture("endpoints_gpt-image-2.5-sunburst.json")
    )
    first = await catalog.endpoints(SUNBURST)
    assert first and first[0]["provider_slug"] == "openai"
    await catalog.endpoints(SUNBURST)
    assert route.call_count == 1
    with pytest.raises(UnknownModelError):
        await catalog.endpoints("nope/nope")


# ---------------------------------------------------------------------- caching


async def test_cache_ttl(catalog, routes, clock):
    images_route, meta_route = routes
    await catalog.all()
    clock.now += 599
    await catalog.all()
    await catalog.get(SUNBURST)
    assert images_route.call_count == 1
    assert meta_route.call_count == 1
    clock.now += 2
    await catalog.all()
    assert images_route.call_count == 2
    assert meta_route.call_count == 2


async def test_stale_cache_used_when_offline(catalog, routes, clock):
    images_route, _ = routes
    first = await catalog.all()
    clock.now += 700
    images_route.mock(side_effect=httpx.ConnectError("offline"))
    again = await catalog.all()
    assert [m.id for m in again] == [m.id for m in first]
    clock.now += 86400
    with pytest.raises(CatalogUnavailableError):
        await catalog.all()


async def test_offline_with_no_cache_raises(client, mock, clock):
    mock.get(f"{BASE}/images/models").mock(side_effect=httpx.ConnectError("offline"))
    mock.get(f"{BASE}/models").respond(200, json={"data": []})
    with pytest.raises(CatalogUnavailableError):
        await Catalog(client, clock=clock).all()


async def test_endpoints_stale_when_offline(catalog, mock, clock):
    route = mock.get(f"{BASE}/images/models/{SUNBURST}/endpoints").respond(
        200, json=fixture("endpoints_gpt-image-2.5-sunburst.json")
    )
    first = await catalog.endpoints(SUNBURST)
    clock.now += 700
    route.mock(side_effect=httpx.ConnectError("offline"))
    assert await catalog.endpoints(SUNBURST) == first


# ------------------------------------------------------------------- validation


async def test_validate_bad_quality_lists_allowed(catalog):
    m = await catalog.get(SUNBURST)
    with pytest.raises(BadRequestError) as exc:
        validate_params(m, **params(quality="ultra"))
    assert "auto, low, medium, high, xhigh, max" in str(exc.value)
    assert f"quality 'ultra' is not supported by {SUNBURST}" in str(exc.value)


def test_validate_ok_returns_no_notes():
    assert (
        validate_params(caps(), **params(aspect_ratio="16:9", quality="low", seed=0))
        == []
    )


def test_validate_n_over_max_returns_note():
    notes = validate_params(caps(max_n=1), **params(n=4))
    assert notes == ["model max n is 1; will make 4 calls"]


def test_validate_n_batches_note_counts_calls():
    notes = validate_params(caps(max_n=4), **params(n=10))
    assert notes == ["model max n is 4; will make 3 calls"]


@pytest.mark.parametrize("n", [0, -1, 11, 100])
def test_validate_n_out_of_range_raises(n):
    with pytest.raises(BadRequestError, match="n must be between 1 and 10"):
        validate_params(caps(max_n=10), **params(n=n))


def test_validate_n_below_one_raises():
    with pytest.raises(BadRequestError):
        validate_params(caps(), **params(n=0))


def test_validate_seed_unsupported_raises():
    with pytest.raises(BadRequestError) as exc:
        validate_params(caps(seed=False), **params(seed=7))
    assert str(exc.value) == "x/y does not support seed"


def test_validate_size_unsupported_raises():
    with pytest.raises(BadRequestError) as exc:
        validate_params(caps(size=False), **params(size="1024x1024"))
    assert str(exc.value) == "x/y does not support size"


def test_validate_unsupported_enum_param_raises():
    with pytest.raises(BadRequestError) as exc:
        validate_params(caps(resolutions=None), **params(resolution="2K"))
    assert str(exc.value) == "x/y does not support resolution"
    with pytest.raises(BadRequestError):
        validate_params(caps(aspect_ratios=None), **params(aspect_ratio="1:1"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("aspect_ratio", "9:16"),
        ("resolution", "8K"),
        ("background", "transparent"),
        ("output_format", "gif"),
    ],
)
def test_validate_enum_values_rejected(field, value):
    with pytest.raises(BadRequestError) as exc:
        validate_params(caps(), **params(**{field: value}))
    assert f"{field} '{value}' is not supported by x/y. Allowed:" in str(exc.value)


def test_validate_too_many_inputs_raises():
    with pytest.raises(BadRequestError) as exc:
        validate_params(caps(max_input_images=4), **params(input_count=5))
    assert "4" in str(exc.value)


def test_validate_inputs_on_text_only_model_raises():
    with pytest.raises(BadRequestError):
        validate_params(
            caps(accepts_images=False, max_input_images=0), **params(input_count=1)
        )


def test_chat_route_rejects_non_aspect_params():
    chat = caps(
        route="chat",
        aspect_ratios=None,
        resolutions=None,
        qualities=None,
        backgrounds=None,
        output_formats=None,
        seed=False,
        size=False,
    )
    with pytest.raises(BadRequestError) as exc:
        validate_params(chat, **params(quality="high"))
    assert "quality" in str(exc.value)
    assert "not supported" in str(exc.value)
    for field, value in [("seed", 3), ("size", "1x1"), ("resolution", "1K")]:
        with pytest.raises(BadRequestError):
            validate_params(chat, **params(**{field: value}))
    # aspect_ratio passes through unvalidated
    assert validate_params(chat, **params(aspect_ratio="7:5")) == []
    assert validate_params(chat, **params(input_count=2)) == []


# ---------------------------------------------------------------------- pricing


def test_price_sort():
    assert (
        price_key(caps(pricing={"image_output": "0.00003", "completion": "9"}))
        == 0.00003
    )
    assert price_key(caps(pricing={"image": "0.04", "completion": "9"})) == 0.04
    assert price_key(caps(pricing={"completion": "0.5"})) == 0.5
    assert price_key(caps(pricing={})) == float("inf")
    assert price_key(caps(pricing={"image_output": "abc", "image": "0.1"})) == 0.1
    assert price_key(caps(pricing={"image_output": None, "completion": "2"})) == 2.0
    assert price_key(caps(pricing={"image_output": "abc"})) == float("inf")
    cheap, mid, unknown = (
        caps(id="a", pricing={"image": "1"}),
        caps(id="b", pricing={"image": "2"}),
        caps(id="c", pricing={}),
    )
    assert [m.id for m in sorted([unknown, mid, cheap], key=price_key)] == [
        "a",
        "b",
        "c",
    ]


def test_price_key_treats_negative_router_pricing_as_unknown():
    # openrouter/auto reports "-1" (variable pricing); it must not sort as cheapest.
    assert price_key(caps(pricing={"prompt": "-1", "completion": "-1"})) == float("inf")
