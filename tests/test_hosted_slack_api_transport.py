"""Transport failures in the Slack SDK reach the bot as `SlackCallError`, never as raw network errors."""

import aiohttp
import httpx
import pytest
from slack_sdk.errors import SlackRequestError

from openrouter_image_mcp.hosted.slack.api import SdkSlackApi, SlackCallError

TOKEN_URL = "https://slack.com/api/chat.update?token=xoxb-" + "9" * 20  # what a raw message could carry


class RaisingClient:
    token = "xoxb-" + "1" * 12

    def __init__(self, error):
        self.error = error

    def __getattr__(self, name):
        async def call(**kwargs):
            raise self.error
        return call


@pytest.mark.parametrize("error", [
    TimeoutError(),
    httpx.ReadTimeout(TOKEN_URL),
    aiohttp.ClientConnectionError(TOKEN_URL),
    aiohttp.ServerDisconnectedError(),
    ConnectionResetError(TOKEN_URL),
    OSError(TOKEN_URL),
])
async def test_network_errors_become_slack_call_errors(error):
    with pytest.raises(SlackCallError) as caught:
        await SdkSlackApi(RaisingClient(error)).update("C1", "1.0", "x")
    assert (caught.value.method, caught.value.code, caught.value.retry_after_s) == ("update", "network", None)
    assert "xoxb-" not in str(caught.value) and caught.value.__suppress_context__


async def test_other_sdk_client_errors_become_slack_call_errors():
    with pytest.raises(SlackCallError) as caught:
        await SdkSlackApi(RaisingClient(SlackRequestError(TOKEN_URL))).post("C1", None, "hi")
    assert caught.value.code == "client_error" and "xoxb-" not in str(caught.value)


async def test_bot_user_id_timeout_is_a_slack_call_error():
    with pytest.raises(SlackCallError) as caught:
        await SdkSlackApi(RaisingClient(TimeoutError())).bot_user_id()
    assert caught.value.code == "network"


async def test_programming_errors_are_not_masked():
    with pytest.raises(KeyError):
        await SdkSlackApi(RaisingClient(KeyError("ts"))).post("C1", None, "hi")
