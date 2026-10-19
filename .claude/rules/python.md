---
globs: ['src/**/*.py', 'scripts/**/*.py', 'tests/**/*.py']
---

# Python Conventions

- Python 3.12+, managed by `uv` (`uv sync`, `uv run ...`); one `pyproject.toml` at the root. Add
  dependencies with `uv add`, never `pip install`, and commit `uv.lock`.
- `uv run ruff check` must pass (pre-commit). Keep ruff's config in `pyproject.toml`.
- snake_case modules and functions, PascalCase classes, UPPER_CASE constants. Type-hint public functions.
- Hosted code lives in `hosted/` and receives its dependencies (stores, vault, clients) as parameters
  so tests pass fakes; keep new code injectable rather than reaching for module globals.
- OpenRouter client failures raise the typed errors in `errors.py`, whose messages pass through
  `logs.redact`; route any new user-facing error text through `redact` too. Never swallow exceptions silently.
- Validate external input (HTTP bodies, env, OpenRouter JSON) at the boundary.
- `scripts/checks/` is stdlib only so the git hooks run before any dependency is importable.
