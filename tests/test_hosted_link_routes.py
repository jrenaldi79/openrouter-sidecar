"""The OpenRouter link pages match the MPD Class Tools sign-in page and stay self-contained."""

import re

from openrouter_image_mcp.hosted import link_routes


def _html(response) -> str:
    return response.body.decode()


def test_pages_carry_the_class_tools_brand():
    page = _html(link_routes._page("Confirm OpenRouter link", "<p>body</p>"))
    assert "MPD Class Tools" in page and "#4E2A84" in page and "#F4F1F8" in page
    assert re.search(r"<img [^>]*src=\"data:image/png;base64,", page)
    assert "<h1>Confirm OpenRouter link</h1>" in page.replace("\n", "")
    assert "jrenaldi@northwestern.edu" in page  # the same help line as the sign-in email


def test_pages_load_nothing_from_elsewhere():
    response = link_routes._page("Linked", "<p>ok</p>")
    page = _html(response)
    assert not re.search(r"(src|href)=[\"']https?://", page)
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "img-src data:" in csp and "form-action 'self'" in csp
    assert response.headers["cache-control"] == "no-store"


def test_titles_and_failures_are_escaped():
    page = _html(link_routes._fail("<script>x</script>"))
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert _html(link_routes._page("<b>", "")).count("<b>") == 0


def test_primary_button_is_styled():
    page = _html(link_routes._page("Confirm", "<form><button type=submit>Link this key</button></form>"))
    assert re.search(r"button\s*\{[^}]*background:\s*#4E2A84", page)
