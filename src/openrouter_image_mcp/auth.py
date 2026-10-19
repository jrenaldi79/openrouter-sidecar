"""OpenRouter OAuth PKCE helpers for the hosted link flow (`hosted/links.py`, `link_routes.py`).

The hosted server keeps the verifier with the link attempt, sends the browser to
OpenRouter with the S256 challenge, and exchanges the returned code on its callback.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import quote, urlencode

from .config import AUTH_URL

__all__ = ["AUTH_URL", "build_auth_url", "challenge_for", "make_pkce"]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge). The verifier is 86 URL-safe characters."""
    verifier = secrets.token_urlsafe(64)
    return verifier, challenge_for(verifier)


def build_auth_url(challenge: str, state: str, workspace_id: str, label: str, *,
                   callback_url: str) -> str:
    params = {
        "callback_url": callback_url,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "key_label": label,
    }
    if workspace_id:
        params["required_workspace_id"] = workspace_id
    return f"{AUTH_URL}?{urlencode(params, quote_via=quote)}"
