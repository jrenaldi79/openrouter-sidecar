"""`team_id` and `bot_channels`: the Slack reads behind the admin page."""

import pytest
from slack_fakes import FakeSlack
from slack_sdk.errors import SlackApiError

from openrouter_image_mcp.hosted.slack.api import SdkSlackApi, SlackApi, SlackCallError


class _Response(dict):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}
        self.data = data


class StubClient:
    """Records the keyword arguments of each AsyncWebClient call it receives."""

    def __init__(self, replies=None, raises=None):
        self.calls, self.replies, self.raises = [], replies or {}, raises

    def __getattr__(self, name):
        async def call(**kwargs):
            self.calls.append((name, kwargs))
            if self.raises:
                raise self.raises
            return _Response(self.replies.get(name, {"ok": True}))
        return call


async def test_team_id():
    client = StubClient({"auth_test": {"ok": True, "team_id": "T9", "user_id": "U1"}})
    assert await SdkSlackApi(client).team_id() == "T9"
    assert client.calls[0][0] == "auth_test"


async def test_team_id_missing_is_slack_call_error():
    with pytest.raises(SlackCallError) as caught:
        await SdkSlackApi(StubClient({"auth_test": {"ok": True}})).team_id()
    assert caught.value.code == "no_team_id"


class PagedClient(StubClient):
    """Serves `users_conversations` pages by cursor."""

    def __init__(self, pages):
        super().__init__()
        self.pages = pages

    async def users_conversations(self, **kwargs):
        self.calls.append(("users_conversations", kwargs))
        return _Response(self.pages[kwargs.get("cursor") or "0"])


def _chan(i, private=False):
    return {"id": f"C{i}", "name": f"chan-{i}", "is_private": private, "num_members": 3}


async def test_bot_channels_pages_and_shapes():
    client = PagedClient({
        "0": {"ok": True, "channels": [_chan(1), _chan(2, True)], "response_metadata": {"next_cursor": "p2"}},
        "p2": {"ok": True, "channels": [_chan(3)], "response_metadata": {"next_cursor": ""}},
    })
    found = await SdkSlackApi(client).bot_channels()
    assert found == [{"id": "C1", "name": "chan-1", "is_private": False},
                     {"id": "C2", "name": "chan-2", "is_private": True},
                     {"id": "C3", "name": "chan-3", "is_private": False}]
    first, second = (kwargs for _, kwargs in client.calls)
    assert first["types"] == "public_channel,private_channel" and first["exclude_archived"] is True
    assert first["limit"] == 200 and "cursor" not in first and second["cursor"] == "p2"


async def test_bot_channels_stops_after_ten_pages():
    page = {"ok": True, "channels": [_chan(1)], "response_metadata": {"next_cursor": "again"}}
    client = PagedClient({"0": page, "again": page})
    assert len(await SdkSlackApi(client).bot_channels()) == 10
    assert len(client.calls) == 10


async def test_bot_channels_error_is_slack_call_error():
    error = SlackApiError("boom", _Response({"ok": False, "error": "ratelimited"}, {"Retry-After": "3"}))
    with pytest.raises(SlackCallError) as caught:
        await SdkSlackApi(StubClient(raises=error)).bot_channels()
    assert (caught.value.method, caught.value.code, caught.value.retry_after_s) == ("bot_channels", "ratelimited", 3.0)


async def test_fake_bot_channels_and_team():
    fake = FakeSlack()
    fake.channels = {"C1": {"name": "alpha", "is_private": True}, "C2": {}}
    fake.bot_in = ["C1", "C2"]
    assert await fake.team_id() == "T0"
    assert await fake.bot_channels() == [{"id": "C1", "name": "alpha", "is_private": True},
                                         {"id": "C2", "name": "C2", "is_private": False}]
    fake.fail_next("bot_channels", "ratelimited")
    with pytest.raises(SlackCallError):
        await fake.bot_channels()
    fake.fail_next("team_id", "ratelimited")
    with pytest.raises(SlackCallError):
        await fake.team_id()


def test_fake_matches_protocol():
    for name in ("team_id", "bot_channels"):
        assert hasattr(SlackApi, name) and hasattr(SdkSlackApi, name) and hasattr(FakeSlack(), name)
