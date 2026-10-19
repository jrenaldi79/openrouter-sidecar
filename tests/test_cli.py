"""The command line serves the hosted server only; stdio and local sign-in are retired.

`serve-http`, `purge` and `active-jobs` are covered in `test_hosted_cli.py`.
"""

import hashlib
import json
import sys

import keyring
import keyring.backend
import keyring.backends.null
import keyring.errors
import pytest
from mcp.server.mcpserver import MCPServer

from openrouter_image_mcp import cli
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.config import COHORT_WORKSPACES, KEYRING_SERVICE
from openrouter_image_mcp.local_bundle import local_store_path

COMMANDS = {"serve-http", "purge", "active-jobs", "export-local"}
RETIRED = ("serve", "login", "logout", "status", "print-config")


def test_no_subcommand_prints_help_and_never_serves_stdio(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise AssertionError("the CLI must not start an MCP server without a command")

    monkeypatch.setattr(MCPServer, "run", refuse)
    assert cli.main([]) == 2
    out = capsys.readouterr().out
    assert out.startswith("usage: openrouter-image-mcp")
    for command in COMMANDS:
        assert command in out


@pytest.mark.parametrize("command", RETIRED)
def test_retired_commands_are_gone(command, capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main([command])
    assert exited.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_local_config_renderers_are_gone():
    for name in ("render_local_config", "render_plugin_config", "SIGNED_OUT_MSG", "LOGOUT_MSG"):
        assert not hasattr(cli, name), name


def _local_chat(keyring_backend, key: str, cohort: str = "2027") -> Scope:
    workspace = COHORT_WORKSPACES[cohort]
    keyring_backend.set_password(KEYRING_SERVICE, workspace, key)
    scope = Scope(hashlib.sha256(key.encode()).hexdigest(), workspace)
    AdvisorStore(local_store_path()).create(scope, "openai/gpt-test", "Critic")
    return scope


def test_export_local_writes_a_private_bundle(tmp_path, monkeypatch, memory_keyring, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _local_chat(memory_keyring, "sk-or-v1-alice")
    out = tmp_path / "export.json"
    assert cli.main(["export-local", "--cohort", "2027", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["chats"][0]["title"] == "Critic"
    printed = capsys.readouterr().out
    assert "Exported 1 chat" in printed and "import your local Sidecar chats" in printed
    if sys.platform != "win32":
        assert out.stat().st_mode & 0o777 == 0o600


def test_export_local_needs_this_cohorts_local_sign_in(tmp_path, monkeypatch, memory_keyring, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _local_chat(memory_keyring, "sk-or-v1-alice", cohort="2027")
    assert cli.main(["export-local", "--cohort", "2028", "--out", str(tmp_path / "x.json")]) == 1
    err = capsys.readouterr().err
    assert "No local sign-in for the Class of 2028" in err and "login" not in err
    assert not (tmp_path / "x.json").exists()


class BrokenKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("locked")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("locked")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("locked")


@pytest.mark.parametrize("backend,message", [
    (BrokenKeyring(), "Couldn't access the OS credential store: locked"),
    (keyring.backends.null.Keyring(), "no plaintext fallback"),
], ids=["locked", "insecure"])
def test_export_local_explains_keyring_problems(tmp_path, memory_keyring, capsys, backend, message):
    keyring.set_keyring(backend)  # memory_keyring fixture restores afterwards
    assert cli.main(["export-local", "--cohort", "2027", "--out", str(tmp_path / "x.json")]) == 1
    assert message in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()
