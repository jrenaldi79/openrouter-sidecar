"""Shared MCP tool plumbing: the server name, expected-error mapping and progress forwarding.

Every tool maps the package's expected errors to a `ToolError`, so the client gets
`isError=True` with a readable message; anything unexpected propagates and the SDK
reports it without details. All text passes through `redact`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .errors import CatalogUnavailableError, OpenRouterError
from .imaging import InputError
from .logs import redact
from .service import ProgressFn

SERVER_NAME = "openrouter-sidecar"
CATALOG_PREFIX = "Couldn't reach the OpenRouter catalog"


@contextmanager
def tool_errors() -> Iterator[None]:
    """Turn the package's expected failures into tool errors (isError=True)."""
    try:
        yield
    except CatalogUnavailableError as exc:
        message = exc.message
        if not message.startswith(CATALOG_PREFIX):
            message = f"{CATALOG_PREFIX}: {message}"
        raise ToolError(redact(message)) from None
    except OpenRouterError as exc:
        raise ToolError(redact(exc.message)) from None
    except (InputError, OSError) as exc:  # e.g. an unreadable input image, disk full
        raise ToolError(redact(str(exc))) from None


def progress_reporter(ctx: Context) -> ProgressFn:
    """Forward service progress (value, total = number of calls) to the client.

    MCP requires progress to increase with every notification, so a value that
    doesn't is nudged just above the last one sent. `Context.report_progress` is a
    no-op when the request carried no progress token.
    """
    last: float | None = None

    async def report(value: float, total: float, message: str) -> None:
        nonlocal last
        if last is not None:
            value = max(last + 1e-6, value)
        last = value
        await ctx.report_progress(value, total, message)

    return report
