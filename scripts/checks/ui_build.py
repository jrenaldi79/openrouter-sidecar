"""Keep the generated MCP App bundles in step with ui/src.

When a commit touches ui sources, the build script, ui dependencies or the bundles
themselves, rebuild with `npm --prefix ui run build` and block if the rebuilt
bundles differ from what is staged (or a new bundle is untracked).
"""

import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from scripts.checks import git

OUTPUT_DIR = "src/openrouter_image_mcp/ui"
TRIGGERS = ("ui/src/", "ui/build.mjs", "ui/package.json", "ui/package-lock.json", f"{OUTPUT_DIR}/")


def needs_build(staged: Iterable[str]) -> bool:
    return any(path.startswith(TRIGGERS) for path in staged)


def stale_outputs(unstaged: Iterable[str], untracked: Iterable[str]) -> list[str]:
    """Only the generated `*.html` bundles count; the folder's note and logo are hand-maintained."""
    return sorted(path for path in {*unstaged, *untracked} if path.endswith(".html"))


def _fail(message: str) -> int:
    print(f"\nBLOCKED: {message}\n", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if not needs_build(git.staged_files("ACMRD")):
        return 0
    npm = shutil.which("npm")
    if npm is None or not Path("ui/node_modules").is_dir():
        return _fail("ui changes need a bundle rebuild; run `npm --prefix ui ci` first.")
    built = subprocess.run([npm, "--prefix", "ui", "run", "build", "--silent"], check=False)
    if built.returncode != 0:
        return _fail("`npm --prefix ui run build` failed.")
    stale = stale_outputs(git.unstaged_changes(OUTPUT_DIR), git.untracked_files(OUTPUT_DIR))
    if stale:
        return _fail("rebuilt MCP App bundles are not staged:\n  " + "\n  ".join(stale)
                     + f"\nReview and `git add {OUTPUT_DIR}`.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
