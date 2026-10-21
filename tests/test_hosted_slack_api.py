import inspect

import httpx
import pytest
from slack_fakes import FakeSlack
from slack_sdk.errors import SlackApiError

from openrouter_image_mcp.hosted.slack.api import (
    SdkSlackApi,
    SlackApi,
    SlackCallError,
    SlackMessage,
)


class _Response(dict):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}
        self.data = data


class StubClient:
    """Records the keyword arguments of each AsyncWebClient call it receives."""

    token = "xoxb-" + "1" * 12

    def __init__(self, replies=None, raises=None):
        self.calls, self.replies, self.raises = [], replies or {}, raises

    def __getattr__(self, name):
        async def call(**kwargs):
            self.calls.append((name, kwargs))
            if self.raises:
                raise self.raises
            return _Response(self.replies.get(name, {"ok": True}))
        return call


async def test_post_disables_unfurl():
    client = StubClient({"chat_postMessage": {"ok": True, "ts": "1.5"}})
    ts = await SdkSlackApi(client).post("C1", "1.0", "hi", username="Claude")
    assert ts == "1.5"
    name, kwargs = client.calls[0]
    assert name == "chat_postMessage"
    assert kwargs["unfurl_links"] is False and kwargs["unfurl_media"] is False
    assert kwargs["thread_ts"] == "1.0" and kwargs["username"] == "Claude"


async def test_post_omits_username_when_absent():
    client = StubClient({"chat_postMessage": {"ts": "2.0"}})
    await SdkSlackApi(client).post("C1", None, "hi")
    assert "username" not in client.calls[0][1]


async def test_slack_error_mapped():
    error = SlackApiError("boom", _Response({"ok": False, "error": "ratelimited"}, {"Retry-After": "7"}))
    api = SdkSlackApi(StubClient(raises=error))
    with pytest.raises(SlackCallError) as caught:
        await api.update("C1", "1.0", "x")
    assert (caught.value.method, caught.value.code, caught.value.retry_after_s) == ("update", "ratelimited", 7.0)


async def test_slack_error_without_retry_after():
    error = SlackApiError("boom", _Response({"ok": False, "error": "channel_not_found"}))
    with pytest.raises(SlackCallError) as caught:
        await SdkSlackApi(StubClient(raises=error)).channel_info("C1")
    assert caught.value.code == "channel_not_found" and caught.value.retry_after_s is None


def test_message_label_from_bot_profile():
    raw = {"ts": "1.0", "text": "hi", "bot_id": "B1", "bot_profile": {"name": "Claude"}}
    assert SlackMessage.from_raw(raw).label == "Claude"


def test_message_label_prefers_username():
    raw = {"ts": "1.0", "text": "hi", "username": "Gem", "bot_profile": {"name": "Claude"}}
    assert SlackMessage.from_raw(raw).label == "Gem"


def test_message_files_and_defaults():
    raw = {"ts": "1.0", "user": "U1", "files": [
        {"id": "F1", "name": "a.png", "mimetype": "image/png", "size": 5, "url_private_download": "https://x/a"}]}
    msg = SlackMessage.from_raw(raw)
    assert msg.text == "" and msg.label is None and msg.user == "U1"
    assert [(f.id, f.name, f.size, f.url) for f in msg.files] == [("F1", "a.png", 5, "https://x/a")]


async def test_replies_page_and_cursor():
    client = StubClient({"conversations_replies": {
        "messages": [{"ts": "1.0", "user": "U1", "text": "a", "thread_ts": "1.0"}],
        "response_metadata": {"next_cursor": "abc"}}})
    page = await SdkSlackApi(client).replies("C1", "1.0", cursor="c0", limit=50)
    assert [m.text for m in page.messages] == ["a"] and page.next_cursor == "abc"
    assert client.calls[0][1] == {"channel": "C1", "ts": "1.0", "limit": 50, "cursor": "c0"}


async def test_history_empty_cursor_is_none():
    client = StubClient({"conversations_history": {"messages": [], "response_metadata": {"next_cursor": ""}}})
    page = await SdkSlackApi(client).history("C1", latest="9.0")
    assert page.next_cursor is None and client.calls[0][1]["latest"] == "9.0"


async def test_pins_members_user_info():
    client = StubClient({
        "pins_list": {"items": [{"type": "message", "message": {"ts": "1.0", "text": "p"}},
                                {"type": "file", "file": {"id": "F"}}]},
        "conversations_members": {"members": ["U1", "U2"], "response_metadata": {"next_cursor": ""}},
        "users_info": {"user": {"id": "U1", "is_restricted": True}},
        "conversations_info": {"channel": {"id": "C1", "is_ext_shared": False}}})
    api = SdkSlackApi(client)
    assert [m.text for m in await api.pins("C1")] == ["p"]
    assert await api.members("C1") == ["U1", "U2"]
    assert (await api.user("U1"))["is_restricted"] is True
    assert (await api.channel_info("C1"))["id"] == "C1"


async def test_members_follows_cursor():
    pages = iter([{"members": ["U1"], "response_metadata": {"next_cursor": "n"}},
                  {"members": ["U2"], "response_metadata": {"next_cursor": ""}}])

    class Paged(StubClient):
        async def conversations_members(self, **kwargs):
            self.calls.append(("conversations_members", kwargs))
            return _Response(next(pages))
    client = Paged()
    assert await SdkSlackApi(client).members("C1") == ["U1", "U2"]
    assert client.calls[1][1]["cursor"] == "n"


async def test_upload_uses_files_upload_v2():
    client = StubClient()
    await SdkSlackApi(client).upload("C1", "1.0", "r.txt", b"data", title="Report")
    name, kwargs = client.calls[0]
    assert name == "files_upload_v2"
    assert kwargs["content"] == b"data" and kwargs["filename"] == "r.txt" and kwargs["title"] == "Report"
    assert kwargs["channel"] == "C1" and kwargs["thread_ts"] == "1.0"


def _api(handler):
    return SdkSlackApi(StubClient(), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_download_sends_bearer_and_returns_bytes():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, content=b"abcdef")
    assert await _api(handler).download("https://files.slack.com/a", 100) == b"abcdef"
    assert seen["auth"] == "Bearer " + StubClient.token


async def test_download_too_large():
    api = _api(lambda request: httpx.Response(200, content=b"x" * 50))
    with pytest.raises(SlackCallError) as caught:
        await api.download("https://files.slack.com/a", 10)
    assert (caught.value.method, caught.value.code) == ("download", "too_large")


async def test_download_http_error():
    api = _api(lambda request: httpx.Response(403))
    with pytest.raises(SlackCallError) as caught:
        await api.download("https://files.slack.com/a", 10)
    assert caught.value.code == "http_403"


def test_fake_and_sdk_signatures_match_protocol():
    for name, spec in inspect.getmembers(SlackApi, inspect.isfunction):
        if name.startswith("_"):
            continue
        for impl in (SdkSlackApi, FakeSlack):
            got = getattr(impl, name) if impl is SdkSlackApi else getattr(FakeSlack(), name)
            params = [p for p in inspect.signature(got).parameters if p != "self"]
            want = [p for p in inspect.signature(spec).parameters if p != "self"]
            assert params == want, (impl.__name__, name)


async def test_fake_slack_fail_next_and_records():
    fake = FakeSlack()
    fake.fail_next("update", "ratelimited", 5)
    with pytest.raises(SlackCallError) as caught:
        await fake.update("C1", "1.0", "x")
    assert caught.value.retry_after_s == 5
    await fake.update("C1", "1.0", "y")
    assert fake.updates == [{"channel": "C1", "ts": "1.0", "text": "y"}]
    ts = await fake.post("C1", None, "hello", username="Gem")
    assert fake.posts[0]["ts"] == ts and fake.posts[0]["username"] == "Gem"
    fake.members["C1"] = ["U1"]
    assert await fake.members("C1") == ["U1"]
