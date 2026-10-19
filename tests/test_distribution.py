"""Checks that the Claude Code plugin, marketplace and README match the hosted server.

Frontmatter and tool-name checks for the skills live in test_skills.py.
"""

import json
import re
from pathlib import Path

from openrouter_image_mcp import __version__
from openrouter_image_mcp.tool_annotations import ANNOTATIONS

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
PLUGIN = ROOT / "plugin"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
README = ROOT / "README.md"
HOSTED_URL = "https://sidecar-production-e110.up.railway.app/mcp"


def _tree(base: Path) -> dict[str, bytes]:
    return {
        p.relative_to(base).as_posix(): p.read_bytes()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def test_plugin_skills_in_sync():
    source = _tree(SKILLS)
    copied = _tree(PLUGIN / "skills")
    assert source, "skills/ is empty"
    assert set(copied) == set(source), (
        "plugin/skills differs from skills/; run: uv run python scripts/sync_plugin_skills.py"
    )
    for name, data in source.items():
        assert copied[name] == data, f"{name} differs; run scripts/sync_plugin_skills.py"


def test_plugin_connects_to_the_hosted_server_over_http():
    actual = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    assert actual == {"mcpServers": {"openrouter-sidecar": {"type": "http", "url": HOSTED_URL}}}


def test_no_stdio_server_configs_remain():
    configs = [p for p in ROOT.rglob(".mcp.json")
               if not {"node_modules", "reference", ".git"} & set(p.relative_to(ROOT).parts)]
    assert configs == [PLUGIN / ".mcp.json"]
    assert not (ROOT / "scripts" / "install_claude_desktop.py").exists()


def test_marketplace_points_to_plugin():
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    entry = data["plugins"][0]
    assert entry["source"] == "./plugin"
    assert entry["name"] == "openrouter-image"


def test_versions_match():
    manifest = json.loads(
        (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == __version__


def test_readme_installs_only_the_hosted_connector():
    text = README.read_text(encoding="utf-8")
    assert HOSTED_URL in text
    assert f"claude mcp add --transport http openrouter-sidecar {HOSTED_URL}" in text
    assert "export-local" in text and "create_local_import" in text  # moving from the local Sidecar
    for retired in ("print-config", "install_claude_desktop", "auth_login", "openrouter-image-mcp login",
                    "OPENROUTER_IMAGE_OUTPUT_DIR", "stdio"):
        assert retired not in text, retired
    assert "git+https" not in text


def test_readme_lists_every_tool():
    text = README.read_text(encoding="utf-8")
    assert set(re.findall(r"^\| `([a-z_]+)` \|", text, re.MULTILINE)) == set(ANNOTATIONS)
