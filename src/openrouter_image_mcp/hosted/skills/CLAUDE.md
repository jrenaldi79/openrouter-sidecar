---
title: "src/openrouter_image_mcp/hosted/skills"
description: "Hosted variants of the class skills, served to claude.ai and Desktop as MCP prompts and skill:// resources."
type: "guidance"
status: "maintained"
read_when: "Changing a class skill, or what hosted clients are told about the workflow."
---

**Owns:** `<name>.md`, one per skill: `name`/`description` frontmatter, then the skill body. `<name>.<file>.md` files are support files served at `skill://<name>/<file>`. `guidance.py` (one folder up) loads them and also holds the server `INSTRUCTIONS`.

**Invariants:**

- These are the hosted counterparts of `skills/` (the Claude Code plugin's source). They describe asset ids, uploads and download links, never local paths, `auth_login` or `output_dir`.
- Every tool named as `` `name(` `` must exist on the hosted server.
- Support files that don't depend on transport, such as the render prompt template, are byte-identical copies of the `skills/` originals.

**Tests:** `tests/test_hosted_guidance.py`.

**Gotchas:** when you change a skill in `skills/`, check whether its hosted variant here needs the same change. This folder note is skipped by the loader.
