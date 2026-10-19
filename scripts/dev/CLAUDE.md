---
title: "scripts/dev"
description: "Local development stack for the hosted sidecar: fake Auth0 identity, fake OpenRouter, a launcher that wires them to a local server, and the synthetic multi-user e2e driver."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in scripts/dev/."
---

**Owns:**

- `fake_identity.py`: an Auth0 stand-in that serves a JWKS and mints test tokens (`.claude/launch.json` `fake-identity`, port 9400).
- `fake_openrouter.py`: an OpenRouter stand-in, including the management API for student keys (create, list, get, disable, delete) behind a fixed fake management key. No real keys, no spend (port 9500).
- `run_sidecar.sh`: runs the hosted server against both fakes. Every endpoint is on 127.0.0.1.
- `e2e_local.py`: drives a running hosted sidecar as several test users, including key provisioning and the instructor's disable, enable and delete.
- `e2e_migration.py`: the M5 leg of that run (local export, then hosted import).

**Invariants:** development only. Nothing here may reach a real Auth0 tenant or OpenRouter, and nothing here ships in the package.

**Tests:** none by design. The synthetic e2e exercises these files (`docs/E2E_LOCAL.md`).

**Gotchas:** state lives in `.dev-state/` (gitignored). The Postgres URL defaults to a local container on port 55433.
