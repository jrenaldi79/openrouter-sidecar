"""OpenRouter key management: the one seam for the management API and `GET /key`.

`KeyManager` holds the org's management key (`OPENROUTER_MANAGEMENT_KEY`) and only
creates, reads, updates and deletes student keys, reads guardrails and assigns each new
key its class budget guardrail; it never runs inference. `current_key` reads a student's own key. Errors never carry either key.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from ..config import API_BASE, APP_TITLE, APP_URL
from ..errors import KEY_REJECTED, AuthRequiredError, ProviderError

_HEADERS = {"HTTP-Referer": APP_URL, "X-Title": APP_TITLE}
_PAGE = 100  # OpenRouter's page size for keys, and the maximum for guardrail listings
# The per-key budget guardrail the instructor keeps in each cohort workspace ($25 a month).
BUDGET_GUARDRAIL = "Sidecar student budget"


class KeyManagementError(ProviderError):
    """A management API call failed; `status` is the HTTP status (None if unreachable)."""

    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class CreatedKey:
    key: str    # plaintext, returned only once by OpenRouter
    hash: str   # OpenRouter's identifier for later management calls
    data: dict


def _client(key: str, transport) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE + "/", timeout=30, transport=transport,
                             headers=_HEADERS | {"Authorization": f"Bearer {key}"})


async def _send(http: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        return await http.request(method, path, **kwargs)
    except httpx.TransportError as exc:
        raise KeyManagementError(f"Couldn't reach OpenRouter ({type(exc).__name__}).") from None


def _json(response: httpx.Response) -> dict:
    if not 200 <= response.status_code < 300:
        # Status only: an error body could echo request details.
        raise KeyManagementError(
            f"OpenRouter key management failed (HTTP {response.status_code}).", response.status_code)
    try:
        body = response.json()
    except ValueError:
        raise KeyManagementError("OpenRouter returned a non-JSON response.", response.status_code) from None
    return body if isinstance(body, dict) else {}


def _path(key_hash: str) -> str:
    return "keys/" + quote(key_hash, safe="")


class KeyManager:
    def __init__(self, management_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self._http = _client(management_key, transport)
        self._budgets: dict[str, str] = {}  # workspace id -> budget guardrail id

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, *, missing_ok: bool = False, **kwargs) -> dict | None:
        response = await _send(self._http, method, path, **kwargs)
        if missing_ok and response.status_code == 404:
            return None
        return _json(response)

    async def create(self, name: str, workspace_id: str) -> CreatedKey:
        """A new key with the workspace's budget guardrail; never an unbudgeted key."""
        guardrail = await self._budget_guardrail(workspace_id)
        body = await self._call("POST", "keys", json={"name": name, "workspace_id": workspace_id})
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        key, key_hash = body.get("key"), data.get("hash")
        if not isinstance(key, str) or not key or not isinstance(key_hash, str) or not key_hash:
            raise KeyManagementError("OpenRouter created a key but did not return it.")
        try:
            await self._call("POST", f"guardrails/{quote(guardrail, safe='')}/assignments/keys",
                             json={"key_hashes": [key_hash]})
        except KeyManagementError:
            self._budgets.pop(workspace_id, None)  # look it up again next time
            await self.delete(key_hash)
            raise
        return CreatedKey(key, key_hash, data)

    async def _budget_guardrail(self, workspace_id: str) -> str:
        # Keys the management key creates don't inherit a budget, so each gets this one.
        if workspace_id not in self._budgets:
            found = next((g.get("id") for g in await self.guardrails(workspace_id)
                          if g.get("name") == BUDGET_GUARDRAIL and g.get("id")), None)
            if found is None:
                raise KeyManagementError(f"No '{BUDGET_GUARDRAIL}' guardrail in workspace {workspace_id}; "
                                         "refusing to create an unbudgeted key.")
            self._budgets[workspace_id] = found
        return self._budgets[workspace_id]

    async def get(self, key_hash: str) -> dict | None:
        body = await self._call("GET", _path(key_hash), missing_ok=True)
        return None if body is None else body.get("data") or {}

    async def delete(self, key_hash: str) -> bool:
        """True if OpenRouter deleted it; False if it was already gone."""
        return await self._call("DELETE", _path(key_hash), missing_ok=True) is not None

    async def set_disabled(self, key_hash: str, disabled: bool) -> dict:
        body = await self._call("PATCH", _path(key_hash), json={"disabled": disabled})
        return body.get("data") or {}

    async def list_keys(self, workspace_id: str) -> list[dict]:
        """Every key in one workspace, disabled ones included."""
        keys: list[dict] = []
        while True:
            params = {"workspace_id": workspace_id, "include_disabled": "true", "offset": len(keys)}
            page = (await self._call("GET", "keys", params=params)).get("data") or []
            keys.extend(page)
            if len(page) < _PAGE:
                return keys

    async def workspace(self, workspace_id: str) -> dict:
        return (await self._call("GET", "workspaces/" + quote(workspace_id, safe=""))).get("data") or {}

    async def _paged(self, path: str, params: dict) -> list[dict]:
        rows: list[dict] = []
        while True:
            body = await self._call("GET", path, params=params | {"limit": _PAGE, "offset": len(rows)})
            page = body.get("data") or []
            rows.extend(page)
            if not page or len(rows) >= int(body.get("total_count") or 0):
                return rows

    async def guardrails(self, workspace_id: str) -> list[dict]:
        return await self._paged("guardrails", {"workspace_id": workspace_id})

    async def key_assignments(self) -> list[dict]:
        """Explicit key-to-guardrail assignments across the organization."""
        return await self._paged("guardrails/assignments/keys", {})


async def current_key(key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """`GET /key` with the student's own key: usage, limit and effective budget."""
    async with _client(key, transport) as http:
        response = await _send(http, "GET", "key")
    if response.status_code == 401:
        raise AuthRequiredError(KEY_REJECTED)
    return _json(response).get("data") or {}
