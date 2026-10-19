"""No-cost acceptance probe of a deployed Sidecar with a real Auth0 token.

Uses the token saved by real_login.py. Checks sign-in, cohort routing, the tool
surface, the signed-in account's provisioned OpenRouter key (account_status issues it on
first use) and the image upload/download path (which proves the asset volume is
writable). It never calls a paid model and never prints the token or a key.

    uv run python scripts/ops/acceptance_probe.py --url https://<sidecar-host>/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from PIL import Image

RESULTS: list[tuple[str, bool]] = []
EXPECTED = {"account_status", "ask_advisor", "wait_advisor_job",
            "create_image_upload", "edit_image", "get_image_download"}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def text(result) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def admitted(url: str, token: str) -> bool:
    """A raw ping: 403 means the account lacks sidecar:use or the profile claim.

    Auth runs before the transport, so a 400 (ping without initialize) still means admitted.
    """
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream",
                                            "Authorization": f"Bearer {token}"})
    print(f"INFO token admission: HTTP {response.status_code}")
    return response.status_code not in (401, 403)


async def probe(url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with (httpx2.AsyncClient(headers=headers, timeout=60) as http,
                Client(streamable_http_client(url, http_client=http), mode="legacy") as client):
        names = {tool.name for tool in (await client.list_tools()).tools}
        check("tool surface is hosted", EXPECTED <= names and not names & {"auth_login", "link_openrouter"},
              str(sorted(EXPECTED - names)))
        status = text(await client.call_tool("account_status", {}))
        check("account_status shows the profile and cohort", "Signed in as" in status and "2027" in status,
              status.splitlines()[0] if status else "")
        # The first account_status issues this account's own class key (no spend).
        check("account_status shows a provisioned key", "OpenRouter key: Sidecar · " in status,
              status.splitlines()[-1] if status else "")
        png = io.BytesIO()
        Image.new("RGB", (32, 24), (78, 42, 132)).save(png, "PNG")
        ticket = (await client.call_tool("create_image_upload", {
            "filename": "acceptance.png", "mime": "image/png", "size_bytes": len(png.getvalue())})).structured_content
        async with httpx.AsyncClient(timeout=60) as plain:
            put = await plain.put(ticket["upload_url"], content=png.getvalue())
        asset = put.json().get("asset_id", "") if put.status_code == 200 else ""
        check("upload stores an asset (volume writable)", asset.startswith("img_"), f"HTTP {put.status_code}")
        if asset:
            links = (await client.call_tool("get_image_download", {"asset_id": asset})).structured_content
            async with httpx.AsyncClient(timeout=60) as plain:
                got = await plain.get(links["image_url"])
            check("signed download returns the upload", got.content == png.getvalue())


async def unauthenticated(url: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream"})
    check("no token is refused with 401", response.status_code == 401)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", type=Path, default=Path(".dev-state/real-token"))
    parser.add_argument("--expect-forbidden", action="store_true",
                        help="the token's account has no sidecar access: only check it is refused")
    args = parser.parse_args()
    token = args.token_file.read_text().strip()
    asyncio.run(unauthenticated(args.url))
    ok = asyncio.run(admitted(args.url, token))
    if args.expect_forbidden:
        check("account without sidecar:use is refused", not ok)
    elif ok:
        asyncio.run(probe(args.url, token))
    else:
        check("token is admitted", False, "403: account lacks sidecar:use or the profile claim")
    passed = sum(ok for _, ok in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} acceptance checks passed (real Auth0 token, no paid calls)")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
