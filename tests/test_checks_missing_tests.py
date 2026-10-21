"""TDD gate: newly added modules must arrive with their flat pytest or Playwright test."""

from scripts.checks.missing_tests import expected_tests, find_missing

PKG = "src/openrouter_image_mcp"


def test_core_and_hosted_modules_map_to_flat_tests():
    assert expected_tests(f"{PKG}/imaging.py") == "tests/test_imaging*.py"
    assert expected_tests(f"{PKG}/hosted/uploads.py") == "tests/test_hosted_uploads*.py"


def test_ui_apps_map_to_playwright_specs():
    assert expected_tests("ui/src/uploader.js", ["ui/src/uploader.html"]) == "ui/tests/uploader*.spec.js"


def test_exempt_paths_have_no_expectation():
    for path in [
        f"{PKG}/__init__.py",
        f"{PKG}/__main__.py",
        f"{PKG}/hosted/__init__.py",
        f"{PKG}/ui/preview.html",
        "scripts/dev/fake_identity.py",
        "tests/helpers.py",
        "ui/src/markdown.js",  # helper without its own app page: covered by app specs
        "docs/E2E_LOCAL.md",
    ]:
        assert expected_tests(path, []) is None, path


def test_prefix_variants_satisfy_the_mapping():
    existing = ["tests/test_imaging_fit.py", "tests/test_hosted_uploads.py", "ui/tests/uploader.spec.js"]
    added = [f"{PKG}/imaging.py", f"{PKG}/hosted/uploads.py", "ui/src/uploader.js", "ui/src/uploader.html"]
    assert find_missing(added, existing) == []


def test_reports_each_new_module_without_a_test():
    added = [f"{PKG}/advisor_client.py", f"{PKG}/hosted/devproxy.py", "ui/src/gallery.js", "ui/src/gallery.html"]
    assert find_missing(added, ["ui/src/gallery.html"]) == [
        (f"{PKG}/advisor_client.py", "tests/test_advisor_client*.py"),
        (f"{PKG}/hosted/devproxy.py", "tests/test_hosted_devproxy*.py"),
        ("ui/src/gallery.js", "ui/tests/gallery*.spec.js"),
    ]


def test_hosted_prefix_does_not_borrow_core_tests():
    assert find_missing([f"{PKG}/hosted/limits.py"], ["tests/test_limits.py"]) == [
        (f"{PKG}/hosted/limits.py", "tests/test_hosted_limits*.py")
    ]


def test_slack_subpackage_needs_test():
    assert expected_tests(f"{PKG}/hosted/slack/parser.py") == "tests/test_hosted_slack_parser*.py"
    assert expected_tests(f"{PKG}/hosted/slack/__init__.py") is None
    assert find_missing([f"{PKG}/hosted/slack/parser.py"], ["tests/test_hosted_parser.py"]) == [
        (f"{PKG}/hosted/slack/parser.py", "tests/test_hosted_slack_parser*.py")
    ]
