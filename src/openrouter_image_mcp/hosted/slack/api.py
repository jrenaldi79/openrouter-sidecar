"""The one Slack I/O seam: every Slack call in the bot goes through `SlackApi`.

`SdkSlackApi` wraps slack_sdk's async client and turns its errors into `SlackCallError`;
tests use `FakeSlack` (tests/slack_fakes.py), which keeps these signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp
import httpx
from slack_sdk.errors import SlackApiError, SlackClientError
from slack_sdk.web.async_client import AsyncWebClient

DOWNLOAD_TIMEOUT_S = 30.0
DOWNLOAD_HOST = "files.slack.com"  # the bot token is only ever sent here
# What the async SDK raises for a timeout or a dropped connection (asyncio.TimeoutError is TimeoutError).
_NETWORK_ERRORS = (aiohttp.ClientError, httpx.HTTPError, TimeoutError, OSError)


class SlackCallError(Exception):
    def __init__(self, method: str, code: str, retry_after_s: float | None = None):
        super().__init__(f"Slack {method} failed: {code}")
        self.method, self.code, self.retry_after_s = method, code, retry_after_s


@dataclass(frozen=True)
class SlackFile:
    id: str
    name: str
    mimetype: str
    size: int
    url: str


@dataclass(frozen=True)
class SlackMessage:
    ts: str
    user: str | None
    text: str
    label: str | None  # for bot posts: `username`, else `bot_profile.name`
    subtype: str | None
    thread_ts: str | None
    files: tuple[SlackFile, ...] = ()

    @classmethod
    def from_raw(cls, raw: dict) -> SlackMessage:
        files = tuple(
            SlackFile(f.get("id", ""), f.get("name") or f.get("title") or "", f.get("mimetype", ""),
                      int(f.get("size") or 0), f.get("url_private_download") or f.get("url_private") or "")
            for f in raw.get("files") or ())
        label = raw.get("username") or (raw.get("bot_profile") or {}).get("name")
        return cls(raw.get("ts", ""), raw.get("user"), raw.get("text") or "", label,
                   raw.get("subtype"), raw.get("thread_ts"), files)


@dataclass(frozen=True)
class Page:
    messages: list[SlackMessage]
    next_cursor: str | None


class SlackApi(Protocol):
    async def bot_user_id(self) -> str: ...
    async def replies(self, channel: str, thread_ts: str, *, cursor: str | None = None,
                      limit: int = 200) -> Page: ...
    async def history(self, channel: str, *, latest: str | None = None, cursor: str | None = None,
                      limit: int = 200) -> Page: ...
    async def pins(self, channel: str) -> list[SlackMessage]: ...
    async def channel_info(self, channel: str) -> dict: ...
    async def members(self, channel: str) -> list[str]: ...
    async def user(self, user_id: str) -> dict: ...
    async def post(self, channel: str, thread_ts: str | None, text: str, *,
                   username: str | None = None) -> str: ...
    async def update(self, channel: str, ts: str, text: str) -> None: ...
    async def upload(self, channel: str, thread_ts: str | None, filename: str, content: bytes, *,
                     title: str, username: str | None = None) -> None: ...
    async def download(self, url: str, max_bytes: int) -> bytes: ...


def _page(response) -> Page:
    cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
    return Page([SlackMessage.from_raw(m) for m in response.get("messages") or ()], cursor)


def _trusted_download(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return parsed.scheme == "https" and parsed.host.lower().rstrip(".") == DOWNLOAD_HOST


def _retry_after(headers) -> float | None:
    try:
        return float(headers.get("Retry-After") or headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


class SdkSlackApi:
    def __init__(self, client: AsyncWebClient, *, http: httpx.AsyncClient | None = None):
        self.client, self._http = client, http

    async def _call(self, method: str, sdk_method: str, **kwargs) -> Any:
        """Every SDK failure leaves as `SlackCallError`. The SDK re-raises raw aiohttp errors and timeouts
        after its retries; those get a fixed code and no context, since their text can hold a request URL."""
        try:
            return await getattr(self.client, sdk_method)(**kwargs)
        except SlackApiError as error:
            response = error.response
            code = str(response.get("error") or "unknown") if response is not None else "unknown"
            raise SlackCallError(method, code, _retry_after(getattr(response, "headers", None))) from error
        except _NETWORK_ERRORS:
            raise SlackCallError(method, "network") from None
        except SlackClientError:
            raise SlackCallError(method, "client_error") from None

    async def aclose(self) -> None:
        """Close the download client this API was given (the web client opens a session per call)."""
        if self._http is not None:
            await self._http.aclose()

    async def bot_user_id(self) -> str:
        """The bot's own user id (`auth.test`), so the bot can find its mention and ignore its own posts."""
        user = (await self._call("bot_user_id", "auth_test")).get("user_id")
        if not isinstance(user, str) or not user:
            raise SlackCallError("bot_user_id", "no_user_id")
        return user

    async def replies(self, channel, thread_ts, *, cursor=None, limit=200) -> Page:
        args = {"channel": channel, "ts": thread_ts, "limit": limit, **({"cursor": cursor} if cursor else {})}
        return _page(await self._call("replies", "conversations_replies", **args))

    async def history(self, channel, *, latest=None, cursor=None, limit=200) -> Page:
        args = {"channel": channel, "limit": limit, **({"latest": latest} if latest else {}),
                **({"cursor": cursor} if cursor else {})}
        return _page(await self._call("history", "conversations_history", **args))

    async def pins(self, channel) -> list[SlackMessage]:
        response = await self._call("pins", "pins_list", channel=channel)
        return [SlackMessage.from_raw(i["message"]) for i in response.get("items") or ()
                if i.get("type") == "message" and i.get("message")]

    async def channel_info(self, channel) -> dict:
        return dict((await self._call("channel_info", "conversations_info", channel=channel))["channel"])

    async def members(self, channel) -> list[str]:
        found: list[str] = []
        cursor = None
        while True:
            args = {"channel": channel, "limit": 200, **({"cursor": cursor} if cursor else {})}
            response = await self._call("members", "conversations_members", **args)
            found += response.get("members") or []
            cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                return found

    async def user(self, user_id) -> dict:
        return dict((await self._call("user", "users_info", user=user_id))["user"])

    async def post(self, channel, thread_ts, text, *, username=None) -> str:
        args = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False,
                **({"thread_ts": thread_ts} if thread_ts else {}), **({"username": username} if username else {})}
        return (await self._call("post", "chat_postMessage", **args))["ts"]

    async def update(self, channel, ts, text) -> None:
        await self._call("update", "chat_update", channel=channel, ts=ts, text=text)

    async def upload(self, channel, thread_ts, filename, content, *, title, username=None) -> None:
        args = {"channel": channel, "filename": filename, "content": content, "title": title,
                **({"thread_ts": thread_ts} if thread_ts else {})}
        await self._call("upload", "files_upload_v2", **args)  # username is not supported by uploads

    async def download(self, url: str, max_bytes: int) -> bytes:
        if not _trusted_download(url):
            raise SlackCallError("download", "untrusted_host")
        http = self._http or httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S)
        try:
            async with http.stream("GET", url, headers={"Authorization": f"Bearer {self.client.token}"},
                                   follow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise SlackCallError("download", f"http_{response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise SlackCallError("download", "too_large")
                return bytes(body)
        except httpx.HTTPError as error:
            raise SlackCallError("download", type(error).__name__) from error
        finally:
            if self._http is None:
                await http.aclose()
