"""Block newly added modules that arrive without a test (TDD). `--all` lists every gap, never fails.

Flat pytest layout: src/openrouter_image_mcp/<m>.py -> tests/test_<m>*.py and
hosted/<m>.py -> tests/test_hosted_<m>*.py, hosted/slack/<m>.py ->
tests/test_hosted_slack_<m>*.py. An MCP App (ui/src/<app>.js with a
sibling <app>.html) -> ui/tests/<app>*.spec.js. Helpers, scripts and dunder
modules are exempt; app specs cover ui helpers.
"""

import sys
from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from scripts.checks import git

PKG = "src/openrouter_image_mcp"
TEST_ROOTS = ("tests", "ui/tests", "ui/src")


def expected_tests(path: str, known: Iterable[str] = ()) -> str | None:
    """Glob of acceptable tests for `path`, or None when it needs none."""
    p = PurePosixPath(path)
    if p.name.startswith("__"):
        return None
    if p.suffix == ".py" and str(p.parent) == PKG:
        return f"tests/test_{p.stem}*.py"
    if p.suffix == ".py" and str(p.parent) == f"{PKG}/hosted":
        return f"tests/test_hosted_{p.stem}*.py"
    if p.suffix == ".py" and str(p.parent) == f"{PKG}/hosted/slack":
        return f"tests/test_hosted_slack_{p.stem}*.py"
    if p.suffix == ".js" and str(p.parent) == "ui/src" and f"ui/src/{p.stem}.html" in set(known):
        return f"ui/tests/{p.stem}*.spec.js"
    return None


def find_missing(added: Iterable[str], existing: Iterable[str]) -> list[tuple[str, str]]:
    added = list(added)
    known = [*existing, *added]
    missing = []
    for path in added:
        pattern = expected_tests(path, known)
        if pattern and not any(fnmatch(candidate, pattern) for candidate in known):
            missing.append((path, pattern))
    return missing


def _existing_files() -> list[str]:
    return [p.as_posix() for root in TEST_ROOTS for p in Path(root).rglob("*") if p.is_file()]


def main(argv: list[str]) -> int:
    report = "--all" in argv
    added = git.tracked_files() if report else git.staged_files("A")
    missing = find_missing(added, _existing_files())
    if not missing:
        return 0
    heading = "Modules without a mapped test:" if report else "BLOCKED: new modules need a test first (TDD):"
    print(f"\n{heading}", file=sys.stderr)
    for path, pattern in missing:
        print(f"  {path}  ->  {pattern}", file=sys.stderr)
    print(file=sys.stderr)
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
