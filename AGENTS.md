# AGENTS.md

Instructions for coding agents other than Claude Code (Codex and others). Claude Code reads `CLAUDE.md`; these
point at the same sources so every harness follows one set of rules.

1. Read `CLAUDE.md`: stack, commands, quality gates and critical gotchas. It applies to every agent.
2. Read the path-scoped rules in `.claude/rules/` (`tdd.md`, `code-quality.md`, `testing.md`, `python.md`, `ui.md`)
   and the `CLAUDE.md` note in each folder you edit.
3. Read `docs/WORKING_AGREEMENTS.md`: secrets, git, deploys, Auth0, schema changes and copy rules.
4. For current work, start from the latest handoff in `docs/superpowers/status/` (now
   `2026-10-22-slack-launch-handoff.md`). Every doc is indexed in `docs/index.md`.

Enable the git hooks once per clone: `git config core.hooksPath .githooks`. Never bypass them.
