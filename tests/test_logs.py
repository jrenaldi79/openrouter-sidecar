import contextlib
import logging

from openrouter_image_mcp import logs


@contextlib.contextmanager
def _configured_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        logs.configure_logging()
        yield logging.getLogger("t")
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_redact():
    assert logs.redact("Bearer sk-or-v1-abc_DEF-123 x") == "Bearer sk-or-*** x"


def test_log_filter_redacts_args(capsys):
    with _configured_logging() as log:
        log.info("key=%s", "sk-or-v1-SECRET_value-9")
        log.info("direct sk-or-v1-OTHER_value-1")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "key=sk-or-***" in captured.err
    assert "SECRET_value" not in captured.err
    assert "OTHER_value" not in captured.err


def test_log_redacts_non_str_args(capsys):
    with _configured_logging() as log:
        log.warning("failed: %s", RuntimeError("bad key sk-or-v1-EXC_secret-1"))
        log.warning("%s", {"Authorization": "Bearer sk-or-v1-HDR_secret-2"})
        log.warning("%(h)s", {"h": ValueError("sk-or-v1-MAP_secret-3")})
    err = capsys.readouterr().err
    assert "failed: bad key sk-or-***" in err
    for leaked in ("EXC_secret", "HDR_secret", "MAP_secret"):
        assert leaked not in err


def test_log_redacts_exception_traceback(capsys):
    with _configured_logging() as log:
        try:
            raise RuntimeError("auth failed with sk-or-v1-TRACE_secret-4")
        except RuntimeError:
            log.exception("request failed")
    err = capsys.readouterr().err
    assert "request failed" in err
    assert "Traceback" in err
    assert "RuntimeError: auth failed with sk-or-***" in err
    assert "TRACE_secret" not in err


def test_redact_withholds_bearer_jwts():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJlbWFpbHxhbGljZSJ9.c2lnbmF0dXJlLXZhbHVlLWhlcmU"
    out = logs.redact(f"Authorization: Bearer {jwt} end")
    assert jwt not in out and "[token withheld]" in out and out.endswith("end")


LINK = "http://127.0.0.1:8000/link/openrouter/start?t=lnk_fd716396cba4899cec1fa4ac6c26f4ee.1792372091.2KIhRlCvT2VK9k-kKJIRRXmdDvLdJ7fWbTLNUswL_Bs"


def test_logs_withhold_link_tokens_and_oauth_codes(capsys):
    with _configured_logging() as log:
        log.info('"GET %s HTTP/1.1" 302', LINK)
        log.info('"GET /link/openrouter/callback?code=abc123XYZ&state=lnk_x HTTP/1.1" 200')
    err = capsys.readouterr().err
    assert "2KIhRlCvT2VK9k" not in err and "lnk_[link withheld]" in err
    assert "abc123XYZ" not in err and "code=[withheld]&state" in err


def test_tool_text_redaction_keeps_link_urls():
    # Users must receive the link in tool output; only logs withhold it.
    assert logs.redact(f"Open {LINK}") == f"Open {LINK}"
