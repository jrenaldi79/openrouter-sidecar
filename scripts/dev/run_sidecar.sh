#!/usr/bin/env bash
# Run the hosted sidecar locally against the fake identity provider and fake
# OpenRouter (scripts/dev/fake_identity.py, scripts/dev/fake_openrouter.py).
# Development only: every external endpoint is on 127.0.0.1.
#
#   DATABASE_URL=postgresql://postgres:test@localhost:55433/sidecar_e2e scripts/dev/run_sidecar.sh
set -euo pipefail

STATE="${SIDECAR_DEV_STATE:-.dev-state}"
PORT="${PORT:-8000}"
mkdir -p "$STATE"
chmod 700 "$STATE"
[ -s "$STATE/vault" ] || python3 -c 'import os,base64;print("v1:"+base64.b64encode(os.urandom(32)).decode())' > "$STATE/vault"
[ -s "$STATE/link" ] || python3 -c 'import secrets;print(secrets.token_urlsafe(48))' > "$STATE/link"
chmod 600 "$STATE/vault" "$STATE/link"

export AUTH0_ISSUER="http://127.0.0.1:${ISSUER_PORT:-9400}/"
export SIDECAR_DEV_INSECURE_ISSUER=1
export SIDECAR_DEV_OPENROUTER_URL="http://127.0.0.1:${OPENROUTER_PORT:-9500}"
export MCP_RESOURCE_URI="http://127.0.0.1:${PORT}/mcp"
export DATABASE_URL="${DATABASE_URL:?set DATABASE_URL to a disposable Postgres database}"
export VAULT_KEYS="$(cat "$STATE/vault")"
export LINK_SIGNING_KEY="$(cat "$STATE/link")"
export ASSET_DIR="${ASSET_DIR:-$(cd "$STATE" && pwd)/assets}"
export HOST=127.0.0.1 PORT

exec uv run openrouter-image-mcp serve-http
