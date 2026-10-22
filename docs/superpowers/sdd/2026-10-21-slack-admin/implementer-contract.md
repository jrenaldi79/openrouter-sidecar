---
title: "Implementer contract (every task of the Slack admin plan)"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): implementer contract handed to every subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Implementer contract (every task of the Slack admin plan)

You implement ONE task of `docs/superpowers/plans/2026-10-21-slack-admin.md` in the Python repo
`openrouter-image-mcp` (OpenRouter Sidecar). It adds a `/admin` web page where MPD admins manage the Slack bot's
channels.

## Where you work

- You run in your own git worktree, created for you on a fresh branch named `worktree-agent-…`. Work only there
  and never cd into another checkout. Planning files are git-ignored and live only in the controller's checkout,
  so read them by absolute path:
  `/Users/john_renaldi/claude-code-projects/mpd-openrouter-sidecar/.claude/worktrees/slack-sidecar/.superpowers/sdd/2026-10-21-slack-admin/`
  (your brief `task-N-brief.md`, `global.md` with the Global Constraints, and this file).
- You can't write outside your worktree. Write your report inside it at
  `.superpowers/sdd/2026-10-21-slack-admin/task-N-report.md` (`.superpowers/` is git-ignored, so it is never
  committed); the controller reads it from there.
- **Base first.** Your worktree is cut from `main`, not from the feature branch. Your dispatch names the base
  commit (the controller's `feat/slack-sidecar` tip). Before anything else:
  1. Check `git branch --show-current` starts with `worktree-agent-` and `git status --short` is empty.
  2. Run exactly `git switch -c admin-task-<N> <base sha>` as its own command, where N is your task number. It
     creates a new branch at the base and switches your clean worktree to it; nothing is discarded, and the empty
     `worktree-agent-…` branch stays as it is. This is the one branch switch the contract allows. Don't use
     `git reset`.
  3. Check `git log -1 --oneline` shows the base commit and that `src/openrouter_image_mcp/hosted/admin/copy.py`
     exists. If the switch is refused or any check fails, stop and report NEEDS_CONTEXT without trying another
     route.

  Then run `uv sync` once.
- Other implementers are working in parallel in other worktrees on other files. Touch only the files your brief
  lists (plus `docs/index.md`, regenerated, and the folder notes it names). If you find you must change another
  file, stop and report NEEDS_CONTEXT naming it.

## Requirements

- Read your brief first: it is your requirements, with the exact values to use verbatim. `global.md` applies to
  every task. The spec (`docs/superpowers/specs/2026-10-21-slack-admin-design.md`, in your worktree) is the
  authority behind the plan; read the sections your task touches.
- Read the repo `CLAUDE.md`, `.claude/rules/*.md`, and the folder `CLAUDE.md` notes of every folder you touch.
- TDD: write the brief's tests first, run them and see them fail, then implement, then run them green.
- Limits: files at most 300 lines, functions at most 50. No token-, key- or secret-shaped literals anywhere,
  tests included; build fakes at runtime. Errors and logs go through `logs.redact`.
- Copy strings come from `hosted/admin/copy.py`; don't redefine them.
- Postgres: a throwaway server is at `TEST_DATABASE_URL="postgresql://$USER@127.0.0.1:54999/sidecar_test"`.
  Run `-m pg` only on your own test files (other implementers share the server); the controller runs the full
  Postgres suite after merging.

## Finishing

- Run `uv run ruff check`, your test files (both backends when they use `db_target`), the full `uv run pytest`,
  `uv run python -m scripts.checks.file_sizes --all`, and `node scripts/self-documentation/generate.js` then
  `--check`.
- Commit with the message your brief gives, ending in a blank line and
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never use `--no-verify`, a bare `git stash`, a push,
  or any branch switch other than the base step. Verify with `git show --stat HEAD` that the commit holds your code.
- Never dispatch subagents, and never a reviewer.
- Write the full report (what you built, decisions, test output summary, concerns) to
  `.superpowers/sdd/2026-10-21-slack-admin/task-N-report.md` inside your own worktree. Return only: status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), your branch
  name (`git branch --show-current`), commit SHA(s) with `git show --stat` output, a one-line test summary, and
  concerns.
