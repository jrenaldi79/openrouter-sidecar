"""Shared tool plumbing: expected failures become tool errors, progress only increases."""

from itertools import pairwise

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from openrouter_image_mcp.errors import (
    CatalogUnavailableError,
    ModerationError,
    ProviderError,
)
from openrouter_image_mcp.imaging import InputError
from openrouter_image_mcp.tool_support import (
    SERVER_NAME,
    progress_reporter,
    tool_errors,
)

SECRET = "sk-or-v1-" + "a" * 64


def test_server_name_is_stable():
    assert SERVER_NAME == "openrouter-sidecar"


def _message(exc: BaseException) -> str:
    with pytest.raises(ToolError) as caught, tool_errors():
        raise exc
    return str(caught.value)


def test_catalog_failures_always_name_the_catalog():
    assert _message(CatalogUnavailableError("network down")) == (
        "Couldn't reach the OpenRouter catalog: network down")
    named = "Couldn't reach the OpenRouter catalog (HTTP 503)."
    assert _message(CatalogUnavailableError(named)) == named


def test_openrouter_input_and_os_errors_are_redacted_tool_errors():
    assert _message(ModerationError(["violence"], provider="OpenAI")).startswith(
        "Blocked by OpenAI moderation")
    leaked = _message(ProviderError(f"bad key {SECRET}"))
    assert SECRET not in leaked and "sk-or-***" in leaked
    assert _message(InputError("Image paths must be absolute")) == "Image paths must be absolute"
    assert "disk full" in _message(OSError("disk full"))


def test_unexpected_errors_propagate():
    with pytest.raises(KeyError), tool_errors():
        raise KeyError("bug")


async def test_progress_guard_keeps_values_strictly_increasing():
    sent = []

    class Ctx:
        async def report_progress(self, progress, total=None, message=None):
            sent.append((progress, total, message))

    report = progress_reporter(Ctx())
    for value in (0.5, 0.5, 0.4, 1.0):
        await report(value, 2, "m")

    values = [v for v, _, _ in sent]
    assert all(b > a for a, b in pairwise(values)), values
    assert values[0] == 0.5 and values[-1] == 1.0
    assert all(t == 2 for _, t, _ in sent)
