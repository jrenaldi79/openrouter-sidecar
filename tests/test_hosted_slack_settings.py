import pytest

from openrouter_image_mcp.hosted.settings import load_hosted_settings
from openrouter_image_mcp.hosted.slack.settings import (
    SlackSettings,
    load_slack_settings,
)

BOT = "xoxb-" + "7" * 20
APP = "xapp-" + "1-" + "8" * 20
ON = {"SIDECAR_SLACK_ENABLED": "true", "SLACK_BOT_TOKEN": BOT, "SLACK_APP_TOKEN": APP}
HOSTED = {
    "AUTH0_ISSUER": "https://tenant.us.auth0.com/",
    "MCP_RESOURCE_URI": "https://sidecar.example/mcp",
    "DATABASE_URL": "postgresql://app@db.railway.internal/sidecar",
    "VAULT_KEYS": "v1:" + "A" * 43 + "=",
    "LINK_SIGNING_KEY": "x" * 32,
}


def test_disabled_by_default():
    s = load_slack_settings({})
    assert s.enabled is False
    assert (s.context_chars, s.daily_cap_usd, s.max_concurrent, s.user_limit) == (8000, 4.0, 3, 10)
    assert s.bot_token is None and s.app_token is None


def test_enabled_loads_tokens_and_overrides():
    s = load_slack_settings({**ON, "SIDECAR_SLACK_CONTEXT_CHARS": "500", "SIDECAR_SLACK_DAILY_CAP_USD": "1.5",
                             "SIDECAR_SLACK_MAX_CONCURRENT": "2", "SIDECAR_SLACK_USER_LIMIT": "5"})
    assert isinstance(s, SlackSettings)
    assert s.enabled and s.bot_token == BOT and s.app_token == APP
    assert (s.context_chars, s.daily_cap_usd, s.max_concurrent, s.user_limit) == (500, 1.5, 2, 5)


@pytest.mark.parametrize("drop", ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"])
def test_enabled_requires_tokens(drop):
    env = {k: v for k, v in ON.items() if k != drop}
    with pytest.raises(ValueError, match=drop):
        load_slack_settings(env)


@pytest.mark.parametrize("key,value", [
    ("SLACK_BOT_TOKEN", "xoxp-" + "7" * 20),
    ("SLACK_APP_TOKEN", "xoxb-" + "7" * 20),
])
def test_token_prefixes_checked(key, value):
    with pytest.raises(ValueError, match=key):
        load_slack_settings({**ON, key: value})


@pytest.mark.parametrize("key,value", [
    ("SIDECAR_SLACK_CONTEXT_CHARS", "0"),
    ("SIDECAR_SLACK_MAX_CONCURRENT", "0"),
    ("SIDECAR_SLACK_USER_LIMIT", "0"),
    ("SIDECAR_SLACK_DAILY_CAP_USD", "-1"),
    ("SIDECAR_SLACK_DAILY_CAP_USD", "lots"),
    ("SIDECAR_SLACK_CONTEXT_CHARS", "many"),
])
def test_numbers_validated(key, value):
    with pytest.raises(ValueError, match=key):
        load_slack_settings({**ON, key: value})


def test_tokens_not_in_repr():
    text = repr(load_slack_settings(ON))
    assert BOT not in text and APP not in text


def test_hosted_settings_carries_slack():
    assert load_hosted_settings(HOSTED).slack.enabled is False
    assert load_hosted_settings({**HOSTED, **ON}).slack.bot_token == BOT
