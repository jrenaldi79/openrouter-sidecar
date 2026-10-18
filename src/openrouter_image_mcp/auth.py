"""OAuth PKCE sign-in with a one-shot localhost callback listener.

``LoginManager.start()`` opens the OpenRouter auth page in the browser and
returns at once. A listener on ``127.0.0.1`` catches the redirect, the code is
exchanged for an API key, and the key goes to the OS keyring. ``wait()`` blocks
until the attempt reaches a terminal state (used by the CLI).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import socket
import socketserver
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import keyring.errors

from . import keystore
from .config import APP_TITLE, AUTH_URL, Settings
from .errors import AuthRequiredError, OpenRouterError
from .logs import redact

log = logging.getLogger(__name__)

_MSG_IDLE = "Not signed in to OpenRouter. Call `auth_login` to sign in."
_MSG_PENDING = "Finish signing in to OpenRouter in your browser. If it didn't open, visit: {url}"
_MSG_SIGNED_IN = "Signed in to OpenRouter."
_MSG_ALREADY = (
    "Already signed in to OpenRouter. "
    "Use switch_account=true to sign in with a different account."
)
_MSG_EXPIRED = (
    "Sign-in timed out after 5 minutes with no response from the browser. "
    "If OpenRouter showed an error about the workspace, ask your admin for an "
    "invite to the organization. Call `auth_login` to try again."
)
_MSG_FAILED = "Sign-in failed: {reason}. Call `auth_login` to try again."

_REASON_DENIED = "authorization was denied in the browser"
_REASON_REJECTED = "the new key was rejected"
_REASON_BAD_CODE = "the login code was invalid or expired"
_REASON_EXCHANGE = "OpenRouter couldn't complete the sign-in; try again shortly"
_REASON_STALLED = "OpenRouter didn't respond while finishing sign-in"

_PAGE_SUCCESS = "Signed in to OpenRouter — you can close this tab and return to Claude."
_PAGE_CANCELLED = "Sign-in to OpenRouter was cancelled. You can close this tab."
_PAGE_BAD_REQUEST = "This sign-in link is not valid. Return to Claude and try again."
_PAGE_INACTIVE = "This sign-in attempt is no longer active. Return to Claude and try again."
_PAGE_NOT_FOUND = "Not found."

_POLL_S = 0.05  # how often the listener thread checks its stop flag
_REQUEST_TIMEOUT_S = 10  # per-connection socket timeout (idle browser preconnects)
_JOIN_TIMEOUT_S = 5
_FINISH_TIMEOUT_S = 60  # bound on each of the code exchange and key check


class LoginState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    SIGNED_IN = "signed_in"
    FAILED = "failed"
    EXPIRED = "expired"


# ------------------------------------------------------------------- PKCE / URL


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge). The verifier is 86 URL-safe characters."""
    verifier = secrets.token_urlsafe(64)
    return verifier, challenge_for(verifier)


def build_auth_url(port: int, challenge: str, state: str, workspace_id: str, label: str,
                   *, callback_url: str | None = None) -> str:
    params = {
        "callback_url": callback_url or f"http://127.0.0.1:{port}/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "key_label": label,
    }
    if workspace_id:
        params["required_workspace_id"] = workspace_id
    return f"{AUTH_URL}?{urlencode(params, quote_via=quote)}"


def default_key_label(cohort: str) -> str:
    return f"{APP_TITLE} Class of {cohort} ({socket.gethostname()})"


# --------------------------------------------------------------------- listener

# (code, error) -> None, called on the listener thread exactly once.
_Notify = Callable[[str | None, str | None], None]


def _page(text: str) -> bytes:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>openrouter-image-mcp</title></head>"
        "<body style=\"font-family: sans-serif; margin: 3em;\">"
        f"<p>{text}</p></body></html>"
    ).encode()


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer
    timeout = _REQUEST_TIMEOUT_S

    def log_message(self, format: str, *args: object) -> None:
        # The request line carries the auth code; never log it.
        pass

    def _reply(self, status: int, text: str) -> None:
        body = _page(text)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != "/callback":
            self._reply(404, _PAGE_NOT_FOUND)
            return
        query = parse_qs(parts.query)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]
        if not self.server.state_matches(state) or not (code or error):
            self._reply(400, _PAGE_BAD_REQUEST)
            return
        if not self.server.claim():
            self._reply(400, _PAGE_INACTIVE)
            return
        try:
            self._reply(200, _PAGE_CANCELLED if error else _PAGE_SUCCESS)
        finally:
            self.server.notify(None if error else code, error or None)


class _CallbackServer(socketserver.ThreadingMixIn, HTTPServer):
    """One-shot listener on 127.0.0.1 with an OS-assigned port."""

    allow_reuse_address = False  # on Windows SO_REUSEADDR would let others steal the port
    daemon_threads = True
    block_on_close = False

    def __init__(self, expected_state: str, notify: _Notify) -> None:
        self._expected = expected_state.encode("ascii")
        self._notify = notify
        self._lock = threading.Lock()
        self.stop = threading.Event()
        super().__init__(("127.0.0.1", 0), _CallbackHandler)
        self.timeout = _POLL_S

    def server_bind(self) -> None:
        # Skip HTTPServer's socket.getfqdn() lookup, which can be slow on Windows.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    @property
    def port(self) -> int:
        return self.server_address[1]

    def state_matches(self, state: str) -> bool:
        return secrets.compare_digest(state.encode("utf-8"), self._expected)

    def claim(self) -> bool:
        """Return True for the first valid callback only, and stop listening."""
        with self._lock:
            if self.stop.is_set():
                return False
            self.stop.set()
            return True

    def notify(self, code: str | None, error: str | None) -> None:
        self._notify(code, error)

    def run(self) -> None:
        """Serve until stopped, then close the socket. Runs on its own thread."""
        try:
            while not self.stop.is_set():
                self.handle_request()
        except Exception:
            log.exception("Sign-in listener crashed")
        finally:
            self.server_close()


# ---------------------------------------------------------------------- manager


@dataclass(eq=False)
class _Attempt:
    verifier: str
    url: str
    server: _CallbackServer
    thread: threading.Thread
    done: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: list[asyncio.Task] = field(default_factory=list)


class LoginManager:
    def __init__(
        self,
        client,
        settings: Settings,
        open_browser: Callable[[str], object] = webbrowser.open,
        timeout_s: float = 300,
    ) -> None:
        self._client = client
        self._settings = settings
        self._open_browser = open_browser
        self._timeout_s = timeout_s
        self._state = LoginState.IDLE
        self._reason = ""
        self._attempt: _Attempt | None = None

    # ------------------------------------------------------------ public

    async def start(self, switch_account: bool = False) -> dict:
        if self._state is LoginState.PENDING:
            return self._start_result()
        has_key = keystore.get_key(workspace_id=self._settings.workspace_id) is not None  # also refuses an insecure keyring early
        if has_key and not switch_account:
            # Drop a stale FAILED/EXPIRED (e.g. from an abandoned switch) so
            # status() falls back to the keystore.
            self._state = LoginState.IDLE
            self._reason = ""
            return {"state": LoginState.SIGNED_IN.value, "url": None, "message": redact(_MSG_ALREADY)}

        loop = asyncio.get_running_loop()
        verifier, challenge = make_pkce()
        state = secrets.token_urlsafe(32)
        attempt: _Attempt | None = None

        def notify(code: str | None, error: str | None) -> None:
            try:
                loop.call_soon_threadsafe(self._on_callback, attempt, code, error)
            except RuntimeError:  # event loop already closed
                pass

        try:
            server = _CallbackServer(state, notify)
        except OSError as exc:
            raise OSError(
                f"Couldn't start the local sign-in listener on 127.0.0.1 ({exc}). Check that "
                "firewall or security software allows local connections, then try again."
            ) from exc
        url = build_auth_url(
            server.port, challenge, state, self._settings.workspace_id, default_key_label(self._settings.cohort)
        )
        thread = threading.Thread(target=server.run, name="openrouter-login", daemon=True)
        attempt = _Attempt(verifier=verifier, url=url, server=server, thread=thread)
        self._attempt = attempt
        self._state = LoginState.PENDING
        self._reason = ""
        thread.start()
        attempt.tasks.append(loop.create_task(self._watchdog(attempt)))

        try:
            self._open_browser(url)
        except Exception:
            log.warning("Couldn't open a browser", exc_info=True)
        return self._start_result()

    def status(self) -> dict:
        state = self._current_state()
        url = self._attempt.url if state is LoginState.PENDING and self._attempt else None
        return {"state": state.value, "message": self._message(state), "url": url}

    async def wait(self) -> LoginState:
        attempt = self._attempt
        if attempt is not None:
            await attempt.done.wait()
            await asyncio.to_thread(attempt.thread.join, _JOIN_TIMEOUT_S)
        return self._current_state()

    def cancel(self) -> None:
        """Abandon a pending attempt (e.g. on shutdown) and close its listener."""
        attempt = self._attempt
        if attempt is None:
            return
        attempt.server.stop.set()
        current = asyncio.current_task() if _loop_running() else None
        for task in attempt.tasks:
            if task is not current and not task.done():
                task.cancel()
        if self._state is LoginState.PENDING:
            self._state = LoginState.IDLE
        attempt.done.set()

    def reset(self) -> None:
        """Cancel any pending attempt and forget a failed/expired one (used by sign-out).

        A late browser callback for the cancelled attempt is ignored.
        """
        self.cancel()
        self._state = LoginState.IDLE
        self._reason = ""

    # ----------------------------------------------------------- internal

    def _current_state(self) -> LoginState:
        if self._state in (LoginState.PENDING, LoginState.FAILED, LoginState.EXPIRED):
            return self._state
        return LoginState.SIGNED_IN if keystore.get_key(workspace_id=self._settings.workspace_id) is not None else LoginState.IDLE

    def _message(self, state: LoginState) -> str:
        if state is LoginState.PENDING:
            text = _MSG_PENDING.format(url=self._attempt.url if self._attempt else "")
        elif state is LoginState.SIGNED_IN:
            text = _MSG_SIGNED_IN
        elif state is LoginState.EXPIRED:
            text = _MSG_EXPIRED
        elif state is LoginState.FAILED:
            text = _MSG_FAILED.format(reason=self._reason)
        else:
            text = _MSG_IDLE
        return redact(text)

    def _start_result(self) -> dict:
        s = self.status()
        return {"state": s["state"], "url": s["url"], "message": s["message"]}

    def _is_live(self, attempt: _Attempt) -> bool:
        return attempt is self._attempt and not attempt.done.is_set()

    def _finish(self, attempt: _Attempt, state: LoginState, reason: str = "") -> None:
        if not self._is_live(attempt):
            return
        self._state = state
        self._reason = reason
        attempt.server.stop.set()  # listener thread closes the socket within _POLL_S
        current = asyncio.current_task()
        for task in attempt.tasks:
            if task is not current and not task.done():
                task.cancel()
        attempt.done.set()

    async def _watchdog(self, attempt: _Attempt) -> None:
        await asyncio.sleep(self._timeout_s)
        self._finish(attempt, LoginState.EXPIRED)

    def _on_callback(self, attempt: _Attempt, code: str | None, error: str | None) -> None:
        if not self._is_live(attempt):
            return
        if error or not code:
            self._finish(attempt, LoginState.FAILED, _REASON_DENIED)
            return
        for task in attempt.tasks:  # the code arrived: no more timeout
            task.cancel()
        attempt.tasks = [asyncio.get_running_loop().create_task(self._complete(attempt, code))]

    async def _complete(self, attempt: _Attempt, code: str) -> None:
        try:
            key = await asyncio.wait_for(
                self._client.exchange_code(code, attempt.verifier), _FINISH_TIMEOUT_S
            )
        except TimeoutError:
            self._finish(attempt, LoginState.FAILED, _REASON_STALLED)
            return
        except AuthRequiredError:
            self._finish(attempt, LoginState.FAILED, _REASON_BAD_CODE)
            return
        except OpenRouterError as exc:
            # Client text can mention generation/billing; keep the reason fixed.
            log.warning("Login code exchange failed: %s", exc.message)
            self._finish(attempt, LoginState.FAILED, _REASON_EXCHANGE)
            return
        except Exception:
            log.exception("Unexpected error exchanging the login code")
            self._finish(attempt, LoginState.FAILED, "an unexpected error occurred")
            return

        try:
            keystore.set_key(key, workspace_id=self._settings.workspace_id)
        except (keystore.InsecureKeyringError, keyring.errors.KeyringError) as exc:
            self._finish(attempt, LoginState.FAILED, f"couldn't store the key ({exc})")
            return

        try:
            await asyncio.wait_for(self._client.key_info(), _FINISH_TIMEOUT_S)
        except TimeoutError:
            # The key is stored; a hung check shouldn't undo the sign-in.
            log.warning("Timed out verifying the new key")
        except AuthRequiredError:
            self._finish(attempt, LoginState.FAILED, _REASON_REJECTED)
            return
        except OpenRouterError as exc:
            # The key is stored; a flaky check shouldn't undo the sign-in.
            log.warning("Couldn't verify the new key: %s", exc.message)
        except Exception:
            log.exception("Unexpected error verifying the new key")
        self._finish(attempt, LoginState.SIGNED_IN)


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
