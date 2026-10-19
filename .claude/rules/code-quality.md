---
globs: ['src/**', 'ui/src/**', 'ui/tests/**', 'scripts/**', 'tests/**']
---

# Code Quality Rules

## Size Limits

| Entity | Max Lines | Enforced By |
| --- | --- | --- |
| **Any code file** (`.py .js .mjs .sh .html .css`) | 300 | `scripts/checks/file_sizes.py` (pre-commit ratchet) |
| **Any function** | 50 | Code review |

The file limit is a **ratchet**: a new file may not exceed 300 lines, and a file already over it
(`service.py`, `imaging.py`, `client.py`, `catalog.py`, several test files) may
shrink or hold steady but never grow. Adding to one of those means extracting something first, into a
sibling module (`advisor_tools.py`, `image_results.py` show the pattern). `--all` lists them.
Generated bundles in `src/openrouter_image_mcp/ui/` and `reference/` are not checked.

## Complexity Red Flags

**STOP and refactor if you see:**

- **>5 nested if/else statements** -> extract functions
- **>3 try/except blocks in one function** -> split error handling
- **>10 imports** -> consider splitting the module
- **Duplicate logic** -> extract a shared helper

## Secrets

Never commit credentials. OpenRouter keys are vault-encrypted per principal; hosted values (`VAULT_KEYS`,
`LINK_SIGNING_KEY`, Auth0, `DATABASE_URL`) come from the environment. `scripts/checks/secret_scan.py`
scans staged files **including tests** for provider keys, JWTs, Auth0/Railway/vault/link-signing values,
non-local Postgres passwords and signed `lnk_`/`upl_`/`img_` tokens. Build fixtures at runtime
(`"v1:" + "A" * 43 + "="`) or keep them obviously fake (`"sk-or-v1-alice"`, `"k" * 32`). Mark a
reviewed false positive with `# pragma: allowlist secret`. Never log tokens, keys or signed links;
`logs.py` redacts keys, JWTs and links — keep it that way.

## Documentation Sync

Adding, removing or renaming files under `src/`, `scripts/` or `ui/`, or adding a doc under `docs/`,
means regenerating the index (`node scripts/self-documentation/generate.js`) in the same commit. The
pre-commit hook validates the staged snapshot.

## Folder Notes

Every folder that directly holds source files below `src/`, `scripts/` or `ui/` has a `CLAUDE.md` note.
Claude Code loads it when you work there, and its `description` heads the folder in `docs/index.md`.
A new folder gets its note in the same commit: five-field frontmatter (`type: "guidance"`,
`status: "maintained"`), then what it owns, entry points, invariants, tests and gotchas — copy a
sibling's shape. Describe what exists now; milestones and "coming in M5" belong in `docs/superpowers/`.
