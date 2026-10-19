"""Get a real Auth0 access token for the hosted Sidecar (operator acceptance only).

Runs the same authorization-code + PKCE flow an MCP client does, with the shared
public client "MPD MCP Clients" and its registered local callback. The person signs
in in their browser; the token is saved to <state>/real-token (mode 600) and never
printed. Development and acceptance only: it holds a live 10-minute token.

    uv run python scripts/ops/real_login.py --audience https://<sidecar-host>/mcp
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

ISSUER = "https://dev-exz4k4zrf6whdd7s.us.auth0.com"
REDIRECT = "http://localhost:33418/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _wait_for_code(state: str) -> str:
    found: dict[str, str] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            ok = query.get("state") == state and "code" in query
            if ok:
                found["code"] = query["code"]
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab." if ok else b"Sign-in failed.")

        def log_message(self, *args):  # keep the code out of the terminal
            pass

    with http.server.HTTPServer(("127.0.0.1", 33418), Callback) as server:
        while "code" not in found:
            server.handle_request()
    return found["code"]


def _client_id(name: str) -> str:
    """The public client's id, looked up with the operator's auth0 CLI session."""
    apps = json.loads(subprocess.run(["auth0", "apps", "list", "--json"], check=True,
                                     capture_output=True, text=True).stdout)
    return next(app["client_id"] for app in apps if app["name"] == name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", help="defaults to the id of --client-name")
    parser.add_argument("--client-name", default="MPD MCP Clients")
    parser.add_argument("--audience", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path(".dev-state"))
    parser.add_argument("--prompt-login", action="store_true",
                        help="ask Auth0 to show the login page even with an existing session")
    # The identifier-first page sends an email to the password database by default;
    # rostered accounts live on the passwordless "email" connection (a code by email).
    parser.add_argument("--connection", default="email",
                        help="Auth0 connection to sign in with; '' shows the full login page")
    args = parser.parse_args()
    args.client_id = args.client_id or _client_id(args.client_name)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    url = f"{ISSUER}/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": args.client_id, "redirect_uri": REDIRECT,
        "audience": args.audience, "scope": "openid email", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        **({"prompt": "login"} if args.prompt_login else {}),
        **({"connection": args.connection} if args.connection else {})})
    print("Opening the Auth0 sign-in page. If it doesn't open, visit:\n" + url, flush=True)
    webbrowser.open(url)
    code = _wait_for_code(state)
    response = httpx.post(f"{ISSUER}/oauth/token", data={
        "grant_type": "authorization_code", "client_id": args.client_id, "code": code,
        "code_verifier": verifier, "redirect_uri": REDIRECT}, timeout=30)
    response.raise_for_status()
    args.state_dir.mkdir(mode=0o700, exist_ok=True)
    target = args.state_dir / "real-token"
    target.write_text(response.json()["access_token"])
    os.chmod(target, 0o600)
    claims = json.loads(base64.urlsafe_b64decode(target.read_text().split(".")[1] + "=="))
    print(f"Saved a token to {target} (audience ok: {args.audience in json.dumps(claims.get('aud'))}, "
          f"permissions: {claims.get('permissions')}, profile claim: "
          f"{'https://mpd.northwestern.edu/profile' in claims}).")


if __name__ == "__main__":
    main()
