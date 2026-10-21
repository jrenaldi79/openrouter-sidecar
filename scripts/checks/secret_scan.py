"""Block staged secrets. `--all` audits every tracked working-tree file instead.

Tests are scanned too: fixtures must be built at runtime (`"v1:" + "A" * 43 + "="`)
or be obviously synthetic (short, or one repeated character), so GitHub secret
scanners never see a real-looking literal. A reviewed line may carry
`# pragma: allowlist secret`.
"""

import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from scripts.checks import git

PRAGMA = "pragma: allowlist secret"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "db", "postgres"}
ALLOWLIST = (
    re.compile(r"^src/openrouter_image_mcp/ui/[^/]+\.html$"),  # generated bundles
    re.compile(r"(^|/)(uv\.lock|package-lock\.json)$"),
)


def _varied(value: str) -> bool:
    """Real keys use many distinct characters; fixtures like "k" * 32 do not."""
    return len(set(value)) >= 8


def _real_postgres(match: re.Match) -> bool:
    password, host = match.group(1), match.group(2).lower()
    placeholder = any(c in password for c in "$<{*") or password.lower() in {"password", "pass", "secret"}
    return host not in LOCAL_HOSTS and not placeholder


def _assigned(name: str) -> str:
    return rf"""(?:{name})["']?\s*[=:]\s*["']?"""


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern
    real: Callable[[re.Match], bool] = field(default=lambda m: m.lastindex is None or _varied(m.group(1)))


PATTERNS = (
    Pattern("OpenRouter API key", re.compile(r"sk-or-(?:v1-)?[A-Za-z0-9]{32,}")),
    Pattern("Anthropic API key", re.compile(r"sk-ant-[\w-]{20,}")),
    Pattern("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    Pattern("GitHub token", re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_\w{30,}")),
    Pattern("Private key block", re.compile(r"-----BEGIN\s[\w\s]*?PRIVATE\sKEY-----")),
    Pattern("JWT", re.compile(r"eyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}")),
    Pattern("Auth0 client secret", re.compile(_assigned("AUTH0_CLIENT_SECRET|client_secret") + r"([\w.~-]{24,})")),
    Pattern("Railway token", re.compile(_assigned(r"RAILWAY_(?:API_)?TOKEN") + r"([\w-]{20,})")),
    Pattern("Vault key", re.compile(r"v1:([A-Za-z0-9+/]{43}=)")),
    Pattern("Link signing key", re.compile(_assigned("LINK_SIGNING_KEY") + r"([\w+/=-]{24,})")),
    Pattern("Postgres URL with password", re.compile(r"postgres(?:ql)?://[^:/\s@]+:([^@\s]+)@([^/:\s]+)"),
            _real_postgres),
    Pattern("Slack token", re.compile(r"xox[abp]-[0-9A-Za-z-]{10,}|xapp-\d-[0-9A-Za-z-]{10,}")),
    Pattern("Signed token", re.compile(r"(?:lnk|upl|img)_[0-9a-f]{32}\.\d+\.[\w.-]+")),
)


@dataclass(frozen=True)
class Finding:
    """Location and pattern name only, so hook output never re-leaks the value."""

    file: str
    line: int
    pattern: str


def is_allowlisted(path: str) -> bool:
    return any(rule.search(path) for rule in ALLOWLIST)


def scan_content(content: str, path: str) -> list[Finding]:
    findings = []
    for number, text in enumerate(content.splitlines(), start=1):
        if PRAGMA in text:
            continue
        for pattern in PATTERNS:
            if any(pattern.real(m) for m in pattern.regex.finditer(text)):
                findings.append(Finding(path, number, pattern.name))
    return findings


def scan_files(paths: Iterable[str], read: Callable[[str], str | None]) -> list[Finding]:
    findings = []
    for path in paths:
        content = None if is_allowlisted(path) else read(path)
        if content is not None:
            findings.extend(scan_content(content, path))
    return findings


def _read_working(path: str) -> str | None:
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return None if "\0" in content else content


def main(argv: list[str]) -> int:
    if "--all" in argv:
        findings = scan_files(git.tracked_files(), _read_working)
    else:
        findings = scan_files(git.staged_files("ACM"), git.read_staged)
    if not findings:
        return 0
    print("\nBLOCKED: possible secrets found:", file=sys.stderr)
    for f in findings:
        print(f"  {f.file}:{f.line}  {f.pattern}", file=sys.stderr)
    print("\nRead real values from the environment; build test fixtures at runtime.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
