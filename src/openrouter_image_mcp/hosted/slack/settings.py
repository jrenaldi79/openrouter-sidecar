"""Slack Sidecar configuration from the environment. Off by default; fails closed when enabled."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

BOT_PREFIX = "xoxb-"
APP_PREFIX = "xapp-"


@dataclass(frozen=True)
class SlackSettings:
    enabled: bool
    # Bot token (xoxb-) posts and reads; app-level token (xapp-) opens the Socket Mode connection.
    bot_token: str | None = field(default=None, repr=False)
    app_token: str | None = field(default=None, repr=False)
    context_chars: int = 8000
    daily_cap_usd: float = 4.0
    max_concurrent: int = 3
    user_limit: int = 10


def _number(env: Mapping[str, str], name: str, default, parse: Callable, minimum_exclusive):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = parse(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number.") from None
    # Written as `not >` so nan fails too.
    if not value > minimum_exclusive:
        raise ValueError(f"{name} must be greater than {minimum_exclusive}.")
    return value


def _token(env: Mapping[str, str], name: str, prefix: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required when SIDECAR_SLACK_ENABLED is set.")
    if not value.startswith(prefix):
        raise ValueError(f"{name} must start with {prefix}")
    return value


def registry_defaults(env: Mapping[str, str]) -> tuple[float, int]:
    """(daily cap in USD, max concurrent jobs) for a new channel row; `slack_channels.py add` uses these
    when its flags are omitted. Needs no tokens, so the ops script can call it."""
    return (_number(env, "SIDECAR_SLACK_DAILY_CAP_USD", 4.0, float, 0),
            _number(env, "SIDECAR_SLACK_MAX_CONCURRENT", 3, int, 0))


def load_slack_settings(env: Mapping[str, str]) -> SlackSettings:
    # Deferred: hosted/settings.py imports this module to build HostedSettings.slack.
    from ..settings import _TRUE

    daily_cap_usd, max_concurrent = registry_defaults(env)
    numbers = {
        "context_chars": _number(env, "SIDECAR_SLACK_CONTEXT_CHARS", 8000, int, 0),
        "daily_cap_usd": daily_cap_usd,
        "max_concurrent": max_concurrent,
        "user_limit": _number(env, "SIDECAR_SLACK_USER_LIMIT", 10, int, 0),
    }
    if env.get("SIDECAR_SLACK_ENABLED", "").lower() not in _TRUE:
        return SlackSettings(enabled=False, **numbers)
    return SlackSettings(
        enabled=True,
        bot_token=_token(env, "SLACK_BOT_TOKEN", BOT_PREFIX),
        app_token=_token(env, "SLACK_APP_TOKEN", APP_PREFIX),
        **numbers,
    )
