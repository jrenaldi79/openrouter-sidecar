---
title: "src/openrouter_image_mcp/hosted/skills"
description: "Hosted variants of the class skills, served to claude.ai and Desktop as MCP prompts and skill:// resources."
type: "guidance"
status: "maintained"
read_when: "Changing a class skill, or what hosted clients are told about the workflow."
---

**Owns:** `<name>.md`, one per skill: `name`/`description` frontmatter, then the skill body. `<name>.<file>.md` files are support files served at `skill://<name>/<file>`. `guidance.py` (one folder up) loads them and also holds the server `INSTRUCTIONS`.

**Invariants:**

- These are the source of the class skills. `skills/<name>/SKILL.md` (the Claude Code plugin's copy) must be byte-identical, since the plugin connects to this server. They describe asset ids, uploads and download links, never local paths or output folders.
- Every tool named as `` `name(` `` must exist on the hosted server.
- Support files, such as the render prompt template, are byte-identical to their `skills/` copies.

**Tests:** `tests/test_hosted_guidance.py`, `tests/test_skills.py`.

**Gotchas:** after changing a skill here, copy it to `skills/<name>/SKILL.md` and run `uv run python scripts/sync_plugin_skills.py`. This folder note is skipped by the loader.
