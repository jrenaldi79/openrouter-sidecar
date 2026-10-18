"""Logging setup: stderr only, with API-key redaction."""

import logging
import re
import sys

_KEY_RE = re.compile(r"sk-or-[A-Za-z0-9_\-]+")
# Bearer JWTs (hosted mode): header.payload.signature, each base64url.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*")
_HANDLER_MARK = "_openrouter_image_mcp"


def redact(text: str) -> str:
    return _JWT_RE.sub("[token withheld]", _KEY_RE.sub("sk-or-***", text))


class RedactingFilter(logging.Filter):
    """Redact the fully formatted message, so non-str args (exceptions, dicts) are covered."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class RedactingFormatter(logging.Formatter):
    """Redact the final output, including exception and stack text."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level=logging.INFO) -> None:
    """Send all logs to stderr (stdout is reserved for MCP) through the redactor."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)
