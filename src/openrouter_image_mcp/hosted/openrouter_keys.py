"""Exchange and verify OpenRouter keys created by the hosted link flow."""

from __future__ import annotations

import httpx

from ..config import API_BASE, APP_TITLE, APP_URL
from ..errors import AuthRequiredError

_HEADERS = {"HTTP-Referer": APP_URL, "X-Title": APP_TITLE}


async def exchange_code(code: str, verifier: str, *, transport=None) -> str:
    async with httpx.AsyncClient(base_url=API_BASE + "/", timeout=30, transport=transport,
                                 headers=_HEADERS) as client:
        response = await client.post("auth/keys", json={
            "code": code, "code_verifier": verifier, "code_challenge_method": "S256"})
    key = response.json().get("key") if response.status_code == 200 else None
    if not isinstance(key, str) or not key:
        raise AuthRequiredError("OpenRouter could not complete the sign-in; try a new link.")
    return key


async def verify_key(key: str, workspace_id: str, *, transport=None) -> str:
    """Confirm the key works; when OpenRouter reports its workspace, it must match.

    The PKCE request already carries required_workspace_id, which OpenRouter
    enforces for membership; this is a defense-in-depth check on the result.
    """
    async with httpx.AsyncClient(base_url=API_BASE + "/", timeout=30, transport=transport,
                                 headers=_HEADERS | {"Authorization": f"Bearer {key}"}) as client:
        response = await client.get("key")
    if response.status_code != 200:
        raise AuthRequiredError("OpenRouter rejected the new key.")
    data = response.json().get("data") or {}
    reported = data.get("workspace_id")
    if reported is not None and reported != workspace_id:
        raise AuthRequiredError("The new key belongs to a different OpenRouter workspace.")
    label = data.get("label")
    return label if isinstance(label, str) and label else "OpenRouter key"
