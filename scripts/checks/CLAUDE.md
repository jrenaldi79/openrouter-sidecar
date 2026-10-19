---
title: "scripts/checks"
description: "Stdlib-only pre-commit gates run by .githooks/pre-commit: secret scan, TDD test mapping, 300-line ratchet and MCP App bundle freshness."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in scripts/checks/."
---

**Owns:**

- `secret_scan.py`: provider keys, JWTs, Auth0, Railway, vault and link-signing values, non-local Postgres passwords, and signed `lnk_`/`upl_`/`img_` tokens. Tests are scanned too.
- `missing_tests.py`: a newly added module needs its flat pytest file or Playwright spec.
- `file_sizes.py`: the ratchet. A new file may not exceed 300 lines, and a file already over may not grow.
- `ui_build.py`: rebuilds bundles when ui files are staged and blocks stale or unstaged output.
- `git.py`: staged, HEAD and tracked readers. It is the only module that shells out to git.

Run with `uv run python -m scripts.checks.<name>`. `--all` on `secret_scan`, `missing_tests` and `file_sizes` audits the whole tree; only `secret_scan --all` fails.

**Invariants:**

- Stdlib only, so the hooks work before any dependency is importable.
- Pre-commit reads staged blobs (`git show :path`), so unstaged edits can't hide what is committed.
- Findings report file, line and pattern name, never the matched value.
- Logic functions take injected readers. `main()` owns argv, output and exit codes.

**Tests:** `tests/test_checks_<module>.py`. `git.py` and the `main()` wrappers are untested.

**Gotchas:**

- Short or single-character fixtures (`"sk-or-v1-alice"`, `"k" * 32`) pass on purpose.
- A reviewed false positive can carry `# pragma: allowlist secret`.
- `ui_build` builds from the working tree, so unstaged ui/src edits affect its result.
