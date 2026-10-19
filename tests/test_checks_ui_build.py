"""Generated MCP App bundles must match ui/src when either side is committed."""

from scripts.checks.ui_build import OUTPUT_DIR, needs_build, stale_outputs


def test_ui_source_build_and_output_changes_trigger_a_rebuild():
    for path in ["ui/src/preview.js", "ui/build.mjs", "ui/package.json", "ui/package-lock.json",
                 f"{OUTPUT_DIR}/preview.html"]:
        assert needs_build([path]), path


def test_unrelated_changes_skip_the_rebuild():
    assert not needs_build(["src/openrouter_image_mcp/server.py", "ui/tests/preview.spec.js", "docs/x.md"])
    assert not needs_build([])


def test_stale_outputs_combine_unstaged_diffs_and_untracked_bundles():
    diff = [f"{OUTPUT_DIR}/preview.html"]
    untracked = [f"{OUTPUT_DIR}/uploader.html"]
    assert stale_outputs(diff, untracked) == [f"{OUTPUT_DIR}/preview.html", f"{OUTPUT_DIR}/uploader.html"]
    assert stale_outputs([], []) == []


def test_only_html_bundles_count_as_stale():
    notes = [f"{OUTPUT_DIR}/CLAUDE.md", f"{OUTPUT_DIR}/northwestern-logo.png"]
    assert stale_outputs(notes, []) == []
