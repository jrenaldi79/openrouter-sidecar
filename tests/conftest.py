import os
import pathlib
import secrets
from urllib.parse import quote

import keyring
import keyring.backend
import keyring.errors
import pytest


class MemoryKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring backend for tests."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self.store[(service, username)]
        except KeyError:
            raise keyring.errors.PasswordDeleteError("not found") from None


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


@pytest.fixture
def no_network_probe(monkeypatch):
    r"""Fail if anything stats a UNC path: on Windows that opens SMB (and can leak NTLM).

    Separators are normalised first, so mixed spellings such as `\/host/share` count.
    """
    seen = []
    for name in ("exists", "is_file", "is_dir", "stat"):
        real = getattr(pathlib.Path, name)

        def guarded(self, *args, _real=real, **kwargs):
            text = str(self)
            if text.replace("/", "\\").startswith("\\\\"):
                seen.append(text)
                raise AssertionError(f"stat of network path {text}")
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, name, guarded)
    return seen


def _pg_schema_url():
    """A fresh Postgres schema for one test; skipped unless TEST_DATABASE_URL is set."""
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set (disposable Postgres)")
    import psycopg

    schema = "t_" + secrets.token_hex(6)
    with psycopg.connect(base, autocommit=True) as db:
        db.execute(f"CREATE SCHEMA {schema}")
    separator = "&" if "?" in base else "?"
    return base, schema, base + separator + "options=" + quote(f"-csearch_path={schema}")


@pytest.fixture(params=["sqlite", pytest.param("pg", marks=pytest.mark.pg)])
def db_target(request, tmp_path):
    """Storage target for either backend: a SQLite file path or a Postgres URL."""
    if request.param == "sqlite":
        yield tmp_path / "advisor.sqlite3"
        return
    base, schema, url = _pg_schema_url()
    yield url
    import psycopg

    from openrouter_image_mcp import storage

    storage.close_pool(url)  # each test's URL gets its own pool; don't let them pile up
    with psycopg.connect(base, autocommit=True) as db:
        db.execute(f"DROP SCHEMA {schema} CASCADE")
