"""300-line ratchet: new files are capped, files already over the limit may not grow."""

from scripts.checks.file_sizes import LIMIT, is_checked, violations


def lines(n):
    return "x\n" * n


def check(new, old=None, path="src/openrouter_image_mcp/m.py"):
    return violations([path], {path: new}.get, {path: old}.get)


def test_limit_is_300():
    assert LIMIT == 300


def test_new_file_over_limit_is_blocked():
    assert check(lines(301)) == [("src/openrouter_image_mcp/m.py", 301, None)]
    assert check(lines(300)) == []


def test_file_crossing_the_limit_is_blocked():
    assert check(lines(310), lines(290)) == [("src/openrouter_image_mcp/m.py", 310, 290)]


def test_oversized_file_may_shrink_or_hold_but_not_grow():
    assert check(lines(716), lines(716)) == []
    assert check(lines(700), lines(716)) == []
    assert check(lines(720), lines(716)) == [("src/openrouter_image_mcp/m.py", 720, 716)]


def test_trailing_line_without_newline_counts():
    assert check(lines(300) + "tail") == [("src/openrouter_image_mcp/m.py", 301, None)]


def test_code_files_are_checked_everywhere_except_generated_and_vendored():
    for path in ["src/openrouter_image_mcp/server.py", "tests/test_server.py", "ui/src/advisor.js",
                 "ui/src/settings.html", "ui/build.mjs", "scripts/dev/run_sidecar.sh"]:
        assert is_checked(path), path
    for path in ["src/openrouter_image_mcp/ui/advisor.html", "uv.lock", "ui/package-lock.json",
                 "docs/superpowers/plans/x.md", "README.md", "reference/ai_polish.py",
                 "tests/fixtures/models_image.json"]:
        assert not is_checked(path), path


def test_unreadable_new_content_is_skipped():
    path = "src/openrouter_image_mcp/m.py"
    assert violations([path], lambda _p: None, lambda _p: None) == []
