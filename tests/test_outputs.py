from io import BytesIO

import pytest
from helpers import make_grid
from PIL import Image

from openrouter_image_mcp import outputs
from openrouter_image_mcp.outputs import encode_image, model_short, prompt_slug


def test_model_short():
    assert model_short("openai/gpt-image-2.5-sunburst") == "gpt-image-2.5-sunburst"
    assert model_short("plain") == "plain"


def test_prompt_slug():
    assert (
        prompt_slug("A Red Panda, astronaut! floating in deep space today")
        == "a-red-panda-astronaut-floating-in"
    )


def test_prompt_slug_empty_and_truncated():
    assert prompt_slug("") == "image"
    assert prompt_slug("  !!! ??? ") == "image"
    slug = prompt_slug(" ".join(["abcdefghijkl"] * 6))
    assert len(slug) <= 60
    assert not slug.endswith("-")
    # Truncation lands just after a "-": the trailing dash is stripped again.
    assert prompt_slug("a" * 59 + " bbbb") == "a" * 59


def test_no_local_file_output_helpers_remain():
    # Results are stored as hosted assets; the stdio server's folders and sidecars are gone.
    for name in ("choose_dir", "output_path", "write_sidecar", "write_bytes_atomic", "unmasked_path"):
        assert not hasattr(outputs, name), name


def test_encode_png_default():
    data, ext = encode_image(make_grid(32, 24), None)
    assert ext == ".png"
    assert Image.open(BytesIO(data)).format == "PNG"


def test_encode_jpeg_flattens_alpha_on_white():
    img = Image.new("RGBA", (8, 8), (255, 0, 0, 0))  # fully transparent
    data, ext = encode_image(img, "jpeg")
    assert ext == ".jpg"
    out = Image.open(BytesIO(data))
    assert out.format == "JPEG"
    r, g, b = out.convert("RGB").getpixel((4, 4))
    assert min(r, g, b) >= 250


def test_encode_webp():
    data, ext = encode_image(make_grid(32, 24), "webp")
    assert ext == ".webp"
    assert Image.open(BytesIO(data)).format == "WEBP"


def test_encode_unknown_format():
    with pytest.raises(ValueError):
        encode_image(make_grid(8, 8), "gif")
