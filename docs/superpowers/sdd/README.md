---
title: "Subagent build records"
description: "Index of the archived briefs, reports, ledgers and rulings from the subagent-driven builds of October 2026"
type: "history"
status: "completed"
read_when: "When tracing why the Slack Sidecar, admin UI, workspace move or sandbox fix was built the way it was"
---

# Subagent build records

These folders hold the planning records from the subagent-driven builds. Each implementer subagent got a brief,
wrote a report, and the controller kept a ledger (`progress.md`) of completions, fix rounds and rulings. They were
working files under the git-ignored `.superpowers/sdd/` and are copied here so any harness (Claude Code, Codex)
can read the full history. Each copied file starts with added frontmatter; the text below it is unchanged.

| Folder | Plan or brief | What it built |
| --- | --- | --- |
| `2026-10-21-slack-sidecar/` | `docs/superpowers/plans/2026-10-21-slack-sidecar.md` | Slack Sidecar, Tasks 0–23 (Task 24 is the live check) |
| `2026-10-21-slack-admin/` | `docs/superpowers/plans/2026-10-21-slack-admin.md` | `/admin` UI, Tasks 1–9 (Task 10 is the live check) |
| `2026-10-21-slack-workspace/` | `brief.md` | One `SLACK_WORKSPACE`, cohort as class label, workspace-gated provisioning |
| `2026-10-21-sandbox-folders/` | `brief.md` | Sandbox folder instructions that match what OpenRouter saves |

Left out on purpose: the per-task review diffs, the red-run test outputs (`task-N-red.txt`) and the `plan-path`
stubs. Recreate any review diff from git with `git diff -U10 <base>..<head>` using the ranges below.

## Review diff ranges

Slack Sidecar: `078f60c..a6ffffd`, `0c166e8..150026d`, `0d44508..0c454fc`, `136cd14..1fbb0bb`, `150026d..f72fa2a`,
`169d95a..333a23e`, `3314b02..b2a9c44`, `38cc95f..5d4bb39`, `4df048f..7bf2065`, `52ba664..dd554dc`,
`709c182..a261e66`, `735ddb2..92b3720`, `745277b..b58ce9c`, `7796820..9e75540`, `797cef2..8ceb81e`,
`7bec6e2..0c166e8`, `7bf2065..3314b02`, `7d41ec8..00280e8`, `923ea99..745277b`, `92b3720..95006e1`,
`976f827..c250d84`, `9aaf168..cf7c854`, `9f0600c..e6074a5`, `a261e66..923ea99`, `a375a74..52ba664`,
`af510a0..b5bec3d`, `b2a9c44..9f0600c`, `b58ce9c..af510a0`, `b5bec3d..976f827`, `c24928f..5114e79`,
`c250d84..a375a74`, `da3b516..7796820`, `dd554dc..4df048f`, `e7dbb57..772ac73`.

Slack admin: `0911c44..b7dc225`, `44f8dbe..ece63f3`, `4f970df..e0198af`, `589ebd6..0911c44`, `589ebd6..4f970df`,
`640d978..ca8ed36`, `d10507a..3e04f29`, `d10507a..50220e0`, `d10507a..893c605`, `d10507a..d788b27`,
`ece63f3..b6dfe7d`, `ece63f3..bc05e0e`, plus the Task 9 docs diff (the docs commits in that task's report).

Workspace move: `9df2fc4` and the merge `d925830` (commits `2ae4c7d`, `c8153dd`). Sandbox folders: `3f14e1a`.
