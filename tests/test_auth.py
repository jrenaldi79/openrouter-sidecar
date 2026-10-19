"""OpenRouter OAuth PKCE helpers used by the hosted link flow."""

import re
from urllib.parse import parse_qs, urlparse

from openrouter_image_mcp.auth import AUTH_URL, build_auth_url, challenge_for, make_pkce

CALLBACK = "https://sidecar.example/openrouter/callback"


def params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_rfc7636_vector():
    # RFC 7636 Appendix B.
    assert (
        challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_make_pkce_lengths():
    verifier, challenge = make_pkce()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    assert challenge == challenge_for(verifier)
    assert make_pkce()[0] != verifier


def test_auth_url_params():
    url = build_auth_url("chal", "st8", "", "Northwestern AI Images Sidecar", callback_url=CALLBACK)
    assert url.startswith(AUTH_URL + "?") and AUTH_URL == "https://openrouter.ai/auth"
    p = params(url)
    assert p["callback_url"] == CALLBACK
    assert p["code_challenge"] == "chal"
    assert p["code_challenge_method"] == "S256"
    assert p["state"] == "st8"
    assert p["key_label"] == "Northwestern AI Images Sidecar"
    assert "required_workspace_id" not in p
    assert "+" not in url  # spaces encoded as %20, not form-style '+'

    p = params(build_auth_url("chal", "st8", "ws-123", "label", callback_url=CALLBACK))
    assert p["required_workspace_id"] == "ws-123"
