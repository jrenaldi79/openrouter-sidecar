"""In-memory `SlackApi` for the Slack bot tests; method signatures match the protocol in hosted/slack/api.py."""

from __future__ import annotations

import inspect

from openrouter_image_mcp.hosted.slack.api import Page, SlackCallError, SlackMessage


class _CallableState(dict):
    """State dict that is also the protocol method of the same name (`history`, `members`)."""

    def __init__(self, call):
        super().__init__()
        self._call = call
        self.__signature__ = inspect.signature(call)  # so signature checks see the protocol method

    def __call__(self, *args, **kwargs):
        return self._call(*args, **kwargs)


class FakeSlack:
    def __init__(self):
        self.channels: dict[str, dict] = {}  # channel id -> conversations.info payload
        self.threads: dict[tuple[str, str], list[SlackMessage]] = {}  # (channel, thread_ts) -> messages
        self.history = _CallableState(self._history)  # channel -> messages, oldest first; call = conversations.history
        self.pinned: dict[str, list[SlackMessage]] = {}
        self.users: dict[str, dict] = {}
        self.members = _CallableState(self._members)  # channel -> user ids; call = conversations.members
        self.files: dict[str, bytes] = {}  # url -> content
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.uploads: list[dict] = []
        self._failures: dict[str, list[tuple[str, float | None]]] = {}
        self._clock = 0
        self.bot_user = "UBOT"  # what auth.test reports
        self.team = "T0"  # the team_id auth.test reports
        self.bot_in: list[str] = []  # channel ids the bot is a member of (users.conversations)

    def fail_next(self, method: str, code: str, retry_after_s: float | None = None) -> None:
        self._failures.setdefault(method, []).append((code, retry_after_s))

    def _check(self, method: str) -> None:
        queued = self._failures.get(method)
        if queued:
            code, retry = queued.pop(0)
            raise SlackCallError(method, code, retry)

    def _ts(self) -> str:
        self._clock += 1
        return f"1700000000.{self._clock:06d}"

    @staticmethod
    def _slice(messages: list[SlackMessage], cursor: str | None, limit: int) -> Page:
        start = int(cursor or 0)
        chunk = messages[start:start + limit]
        more = start + limit < len(messages)
        return Page(list(chunk), str(start + limit) if more else None)

    async def bot_user_id(self) -> str:
        self._check("bot_user_id")
        return self.bot_user

    async def team_id(self) -> str:
        self._check("team_id")
        return self.team

    async def bot_channels(self) -> list[dict]:
        self._check("bot_channels")
        return [{"id": c, "name": self.channels.get(c, {}).get("name", c),
                 "is_private": self.channels.get(c, {}).get("is_private", False)} for c in self.bot_in]

    async def replies(self, channel, thread_ts, *, cursor=None, limit=200) -> Page:
        self._check("replies")
        return self._slice(self.threads.get((channel, thread_ts), []), cursor, limit)

    async def _history(self, channel, *, latest=None, cursor=None, limit=200) -> Page:
        self._check("history")
        messages = [m for m in self.history.get(channel, []) if latest is None or float(m.ts) <= float(latest)]
        return self._slice(list(reversed(messages)), cursor, limit)  # Slack returns newest first

    async def pins(self, channel) -> list[SlackMessage]:
        self._check("pins")
        return list(self.pinned.get(channel, []))

    async def channel_info(self, channel) -> dict:
        self._check("channel_info")
        if channel not in self.channels:
            raise SlackCallError("channel_info", "channel_not_found")
        return dict(self.channels[channel])

    async def _members(self, channel) -> list[str]:
        self._check("members")
        return list(self.members.get(channel, []))

    async def user(self, user_id) -> dict:
        self._check("user")
        if user_id not in self.users:
            raise SlackCallError("user", "user_not_found")
        return dict(self.users[user_id])

    async def post(self, channel, thread_ts, text, *, username=None) -> str:
        self._check("post")
        ts = self._ts()
        self.posts.append({"channel": channel, "thread_ts": thread_ts, "text": text, "username": username, "ts": ts})
        return ts

    async def update(self, channel, ts, text) -> None:
        self._check("update")
        self.updates.append({"channel": channel, "ts": ts, "text": text})

    async def upload(self, channel, thread_ts, filename, content, *, title, username=None) -> None:
        self._check("upload")
        self.uploads.append({"channel": channel, "thread_ts": thread_ts, "filename": filename,
                             "content": content, "title": title, "username": username})

    async def download(self, url: str, max_bytes: int) -> bytes:
        self._check("download")
        if url not in self.files:
            raise SlackCallError("download", "http_404")
        if len(self.files[url]) > max_bytes:
            raise SlackCallError("download", "too_large")
        return self.files[url]
