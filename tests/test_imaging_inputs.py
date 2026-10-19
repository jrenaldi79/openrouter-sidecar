import base64
import hashlib
from io import BytesIO

import pytest
from helpers import make_grid, save, unc_paths
from PIL import Image

from openrouter_image_mcp.imaging import (
    InputError,
    PreparedImage,
    check_mask,
    is_network_path,
    make_preview,
    prepare_input,
    resolve_user_path,
)


@pytest.mark.parametrize("path", [*unc_paths("x.png"), r"\\?\C:\x.png", r"\\.\pipe\x", "//evil-host"])
def test_is_network_path_true(path):
    assert is_network_path(path)


@pytest.mark.parametrize("path", [r"C:\x.png", "C:/x.png", "/home/u/x.png", "~/x.png", "rel/x.png", r"\x.png", ""])
def test_is_network_path_false(path):
    assert not is_network_path(path)


@pytest.mark.parametrize("path", unc_paths("x.png"))
def test_network_paths_rejected_without_touching_them(path, no_network_probe):
    with pytest.raises(InputError, match="Network .*paths aren't supported"):
        resolve_user_path(path)
    with pytest.raises(InputError, match="Network .*paths aren't supported"):
        check_mask(path)
    with pytest.raises(InputError, match="Network .*paths aren't supported"):
        prepare_input(path, 2048)
    assert no_network_probe == []


def test_relative_path_rejected():
    with pytest.raises(InputError):
        resolve_user_path("relative/pic.png")


def test_missing_file_rejected(tmp_path):
    with pytest.raises(InputError):
        resolve_user_path(str(tmp_path / "nope.png"))


def test_tilde_expanded(tmp_path, monkeypatch):
    save(make_grid(10, 10), tmp_path / "a.png", "PNG")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert resolve_user_path("~/a.png") == tmp_path / "a.png"


def test_directory_rejected(tmp_path):
    with pytest.raises(InputError):
        resolve_user_path(str(tmp_path))


def test_unsupported_format_rejected(tmp_path):
    p = save(make_grid(20, 20), tmp_path / "x.gif", "GIF")
    with pytest.raises(InputError):
        prepare_input(str(p), 2048)


def test_non_image_rejected(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"not an image")
    with pytest.raises(InputError):
        prepare_input(str(p), 2048)


def test_exif_rotation_applied(tmp_path):
    p = save(make_grid(200, 100), tmp_path / "r.jpg", "JPEG", exif_orientation=6)
    prep = prepare_input(str(p), 2048)
    assert prep.original_size == (100, 200)
    assert prep.original.size == (100, 200)
    assert prep.sent_size == (100, 200)


def test_jpeg_reencoded_without_exif(tmp_path):
    p = save(make_grid(200, 100), tmp_path / "r.jpg", "JPEG", exif_orientation=3)
    assert len(Image.open(p).getexif()) > 0
    prep = prepare_input(str(p), 2048)
    assert prep.mime == "image/jpeg"
    out = Image.open(BytesIO(prep.encoded))
    assert out.format == "JPEG"
    assert len(out.getexif()) == 0


def test_png_and_alpha_stay_png(tmp_path):
    img = Image.new("RGBA", (40, 30), (10, 20, 30, 128))
    p = save(img, tmp_path / "a.png", "PNG")
    prep = prepare_input(str(p), 2048)
    assert prep.mime == "image/png"
    assert prep.original.mode == "RGBA"
    assert Image.open(BytesIO(prep.encoded)).mode == "RGBA"


def test_opaque_png_stays_png(tmp_path):
    p = save(make_grid(40, 30), tmp_path / "o.png", "PNG")
    prep = prepare_input(str(p), 2048)
    assert prep.mime == "image/png"
    assert Image.open(BytesIO(prep.encoded)).format == "PNG"


def test_png_content_in_jpg_extension_is_png(tmp_path):
    p = save(make_grid(40, 30), tmp_path / "liar.jpg", "PNG")
    assert prepare_input(str(p), 2048).mime == "image/png"


def test_jpeg_content_in_png_extension_is_jpeg(tmp_path):
    p = save(make_grid(40, 30), tmp_path / "liar.png", "JPEG")
    assert prepare_input(str(p), 2048).mime == "image/jpeg"


def test_palette_with_transparency_has_alpha(tmp_path):
    img = Image.new("P", (20, 20), 0)
    img.putpalette([255, 0, 0, 0, 255, 0] + [0] * 250 * 3)
    p = tmp_path / "p.png"
    img.save(p, format="PNG", transparency=0)
    prep = prepare_input(str(p), 2048)
    assert prep.original.mode == "RGBA"
    assert prep.mime == "image/png"


@pytest.mark.parametrize("fmt", ["WEBP", "TIFF", "BMP"])
def test_other_supported_formats(tmp_path, fmt):
    p = save(make_grid(64, 48), tmp_path / f"x.{fmt.lower()}", fmt)
    prep = prepare_input(str(p), 2048)
    assert prep.original_size == (64, 48)
    assert prep.original.mode == "RGB"


@pytest.mark.parametrize("fmt", ["TIFF", "BMP"])
def test_tiff_bmp_without_alpha_encode_jpeg(tmp_path, fmt):
    p = save(make_grid(64, 48), tmp_path / f"x.{fmt.lower()}", fmt)
    prep = prepare_input(str(p), 2048)
    assert prep.mime == "image/jpeg"
    assert Image.open(BytesIO(prep.encoded)).format == "JPEG"


def test_rgba_tiff_encodes_png(tmp_path):
    p = save(Image.new("RGBA", (16, 16), (1, 2, 3, 100)), tmp_path / "a.tiff", "TIFF")
    prep = prepare_input(str(p), 2048)
    assert prep.mime == "image/png"
    assert prep.original.mode == "RGBA"


def test_16bit_converted(tmp_path):
    img = Image.new("I;16", (32, 32), 40000)
    p = save(img, tmp_path / "g.png", "PNG")
    assert Image.open(p).mode.startswith("I")
    prep = prepare_input(str(p), 2048)
    assert prep.original.mode == "RGB"
    assert Image.open(BytesIO(prep.encoded)).mode == "RGB"
    # 40000 / 257 is about 155: scaled down to 8-bit, not clipped to white
    r, g, b = prep.original.getpixel((5, 5))
    assert r == g == b
    assert 150 <= r <= 160


@pytest.mark.parametrize("fmt", ["TIFF", "JPEG"])
def test_cmyk_converted(tmp_path, fmt):
    p = save(Image.new("CMYK", (32, 32), (0, 0, 0, 0)), tmp_path / f"c.{fmt.lower()}", fmt)
    prep = prepare_input(str(p), 2048)
    assert prep.original.mode == "RGB"
    assert Image.open(BytesIO(prep.encoded)).mode == "RGB"


def test_grayscale_converted_to_rgb(tmp_path):
    p = save(Image.new("L", (32, 32), 100), tmp_path / "l.jpg", "JPEG")
    assert prepare_input(str(p), 2048).original.mode == "RGB"


def test_downscale_only_sent_copy(tmp_path):
    p = save(make_grid(7680, 3300), tmp_path / "big.png", "PNG")
    prep = prepare_input(str(p), 2048)
    assert prep.sent_size == (2048, 880)
    assert prep.original_size == (7680, 3300)
    assert prep.original.size == (7680, 3300)
    assert Image.open(BytesIO(prep.encoded)).size == (2048, 880)


def test_1920x828_not_downscaled(tmp_path):
    p = save(make_grid(1920, 828), tmp_path / "w.jpg", "JPEG")
    prep = prepare_input(str(p), 2048)
    assert prep.sent_size == (1920, 828)
    assert Image.open(BytesIO(prep.encoded)).size == (1920, 828)


def test_sha256_is_of_file_bytes(tmp_path):
    p = save(make_grid(50, 50), tmp_path / "h.jpg", "JPEG", exif_orientation=6)
    prep = prepare_input(str(p), 2048)
    assert prep.sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    assert prep.path == p


def test_data_url(tmp_path):
    p = save(make_grid(30, 30), tmp_path / "d.png", "PNG")
    prep = prepare_input(str(p), 2048)
    assert isinstance(prep, PreparedImage)
    prefix = "data:image/png;base64,"
    assert prep.data_url.startswith(prefix)
    assert base64.b64decode(prep.data_url[len(prefix):]) == prep.encoded


def test_path_with_spaces_commas_unicode(tmp_path):
    d = tmp_path / "Example Firm, INC" / "01 Personal" / "Café renders"
    p = save(make_grid(64, 48), d / "vue été.png", "PNG")
    prep = prepare_input(str(p), 2048)
    assert prep.original_size == (64, 48)
    assert prep.path == p


def test_invalid_max_edge(tmp_path):
    p = save(make_grid(10, 10), tmp_path / "a.png", "PNG")
    with pytest.raises(InputError):
        prepare_input(str(p), 0)


def test_preview_bounds():
    out = make_preview(make_grid(1920, 828))
    img = Image.open(BytesIO(out))
    assert img.format == "JPEG"
    assert max(img.size) == 1024
    assert img.size == (1024, 442)


def test_preview_not_upscaled():
    img = Image.open(BytesIO(make_preview(make_grid(200, 100))))
    assert img.size == (200, 100)


def test_preview_rgba_flattened_on_white():
    out = make_preview(Image.new("RGBA", (50, 50), (255, 0, 0, 0)))
    img = Image.open(BytesIO(out))
    assert img.mode == "RGB"
    r, g, b = img.getpixel((25, 25))
    assert r > 240 and g > 240 and b > 240


def test_check_mask_returns_resolved_path(tmp_path):
    mask = save(Image.new("L", (8, 8), 255), tmp_path / "mask.png", "PNG")
    assert check_mask(str(mask)) == mask


def test_check_mask_rejects_undecodable_file(tmp_path):
    bad = tmp_path / "mask.png"
    bad.write_bytes(b"this is not an image")
    with pytest.raises(InputError, match="Cannot read mask"):
        check_mask(str(bad))


def test_check_mask_rejects_relative_path():
    with pytest.raises(InputError, match="absolute"):
        check_mask("relative/mask.png")


