"""Pre-commit secret scan: provider keys, hosted-sidecar credentials and signed tokens.

Secret-shaped fixtures are built at runtime so this file never holds a literal.
"""

import base64

from scripts.checks.secret_scan import is_allowlisted, scan_content, scan_files

HEX32 = "fd716396cba4899cec1fa4ac6c26f4ee"
VARIED = "Q7xk2Lp9Rm4Tz8Vb1Nc6Hd3Jf5Gs0Wa"  # many distinct characters, like a real key


def names(content, path="src/openrouter_image_mcp/x.py"):
    return [f.pattern for f in scan_content(content, path)]


def test_provider_keys_are_caught():
    assert names("key = '" + "sk-or-v1-" + "a1" * 32 + "'") == ["OpenRouter API key"]
    assert names("sk-ant-" + "api03-" + "b" * 30) == ["Anthropic API key"]
    assert names("AKIA" + "ABCDEFGHIJKLMNOP") == ["AWS access key"]
    assert names("ghp_" + "c" * 36) == ["GitHub token"]
    assert names("-----BEGIN " + "RSA PRIVATE KEY-----") == ["Private key block"]


def test_short_synthetic_values_used_in_tests_pass():
    assert names('key = "' + "sk-or-v1-" + 'alice"') == []
    assert names('LINK_SIGNING_KEY = "' + "k" * 32 + '"') == []
    assert names('VAULT_KEYS="' + "v1:" + "A" * 43 + '="') == []


def test_jwt_literal_is_caught_even_in_tests():
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiJhbGljZSJ9" + "." + "S" * 20
    assert [f.pattern for f in scan_content(jwt, "tests/test_logs.py")] == ["JWT"]


def test_auth0_and_railway_assignments():
    assert names("AUTH0_CLIENT_SECRET=" + VARIED * 2) == ["Auth0 client secret"]
    assert names('"client_secret": "' + VARIED + '"') == ["Auth0 client secret"]
    assert names("RAILWAY_TOKEN=" + VARIED) == ["Railway token"]
    assert names("RAILWAY_API_TOKEN: " + VARIED) == ["Railway token"]
    assert names("client_secret=os.environ['AUTH0_CLIENT_SECRET']") == []


def test_vault_and_link_signing_keys():
    assert names("v1:" + base64.b64encode(bytes(range(1, 33))).decode()) == ["Vault key"]
    assert names("LINK_SIGNING_KEY=" + VARIED) == ["Link signing key"]


def test_postgres_urls_with_real_passwords():
    assert names("postgresql://app:" + VARIED + "@db.railway.internal/sidecar") == [
        "Postgres URL with password"
    ]
    assert names("postgres://postgres:test@localhost:5432/postgres") == []
    assert names("postgresql://postgres:test@127.0.0.1:55433/e2e") == []
    assert names("postgresql://app@db.railway.internal/sidecar") == []
    assert names("postgresql://user:${PGPASSWORD}@db.example.com/x") == []
    assert names("postgresql://user:<password>@db.example.com/x") == []


def test_signed_link_upload_and_asset_tokens():
    assert names("t=lnk_" + HEX32 + ".1792372091.2KIhRlCvT2VK9k-kKJIRRXmd") == ["Signed token"]
    assert names("/uploads/upl_" + HEX32 + ".1792372091.sig") == ["Signed token"]
    assert names("/assets/img_" + HEX32 + ".1792372091.0.2KIhRlCvT2VK9k-kK") == ["Signed token"]
    assert names("state=lnk_x") == []


def test_findings_carry_location_but_never_the_value():
    content = "ok\n" + "AKIA" + "ABCDEFGHIJKLMNOP\n"
    (finding,) = scan_content(content, "src/a.py")
    assert (finding.file, finding.line, finding.pattern) == ("src/a.py", 2, "AWS access key")
    assert "ABCDEFGH" not in repr(finding)


def test_inline_pragma_allows_a_reviewed_line():
    assert names("AKIA" + "ABCDEFGHIJKLMNOP  # pragma: allowlist secret") == []


def test_generated_bundles_and_lockfiles_are_skipped():
    assert is_allowlisted("src/openrouter_image_mcp/ui/preview.html")
    assert is_allowlisted("uv.lock")
    assert is_allowlisted("ui/package-lock.json")
    assert not is_allowlisted("tests/test_logs.py")
    assert not is_allowlisted("docs/HOSTED_OPERATIONS.md")


def test_scan_files_skips_unreadable_and_allowlisted():
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    files = {"a.py": key, "uv.lock": key, "gone.py": None}
    found = scan_files(list(files), files.get)
    assert [f.file for f in found] == ["a.py"]
