"""300-line ratchet on staged code files. `--all` lists oversized tracked files, never fails.

A new file may not exceed the limit; a file already over it (grandfathered) may
shrink or hold steady but not grow: blocked when new > max(LIMIT, committed).
"""

import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from scripts.checks import git

LIMIT = 300
CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".html", ".css"}
SKIPPED_PREFIXES = ("src/openrouter_image_mcp/ui/", "reference/")  # generated bundles, vendored reference

Reader = Callable[[str], str | None]


def is_checked(path: str) -> bool:
    return PurePosixPath(path).suffix in CODE_SUFFIXES and not path.startswith(SKIPPED_PREFIXES)


def count_lines(content: str) -> int:
    return content.count("\n") + (0 if content == "" or content.endswith("\n") else 1)


def violations(paths: Iterable[str], read_new: Reader, read_old: Reader) -> list[tuple[str, int, int | None]]:
    found = []
    for path in filter(is_checked, paths):
        new = read_new(path)
        if new is None:
            continue
        old = read_old(path)
        new_lines, old_lines = count_lines(new), None if old is None else count_lines(old)
        if new_lines > max(LIMIT, old_lines or 0):
            found.append((path, new_lines, old_lines))
    return found


def _report_all() -> int:
    sizes = []
    for path in filter(is_checked, git.tracked_files()):
        try:
            sizes.append((count_lines(Path(path).read_text(encoding="utf-8", errors="replace")), path))
        except OSError:
            continue
    for lines, path in sorted(sizes, reverse=True):
        if lines > LIMIT:
            print(f"  {lines:5d}  {path}")
    return 0


def main(argv: list[str]) -> int:
    if "--all" in argv:
        return _report_all()
    found = violations(git.staged_files("ACMR"), git.read_staged, git.read_head)
    if not found:
        return 0
    print(f"\nBLOCKED: files over the {LIMIT}-line limit:", file=sys.stderr)
    for path, new, old in found:
        detail = "new file" if old is None else f"was {old}"
        print(f"  {path}: {new} lines ({detail})", file=sys.stderr)
    print("\nSplit the module. Files already over the limit may shrink but not grow.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
