import json
import sys
import types
from typing import ClassVar

import keyring
import keyring.backend
import keyring.errors
import pytest

from openrouter_image_mcp import auth, cli, keystore
from openrouter_image_mcp.auth import LoginState
from openrouter_image_mcp.errors import OpenRouterError


@pytest.fixture(autouse=True)
def selected_cohort(monkeypatch):
    monkeypatch.setenv("OPENROUTER_IMAGE_COHORT", "2027")
    monkeypatch.delenv("OPENROUTER_IMAGE_WORKSPACE_ID", raising=False)


def test_default_is_serve(monkeypatch):
    calls = []
    fake = types.ModuleType("openrouter_image_mcp.server")
    fake.run = lambda: calls.append("run")
    monkeypatch.setitem(sys.modules, "openrouter_image_mcp.server", fake)
    monkeypatch.setattr(cli, "configure_logging", lambda: calls.append("log"))
    assert cli.main([]) == 0
    assert calls == ["log", "run"]


def test_print_config_runs_checkout(capsys):
    assert cli.main(["print-config", "--cohort", "2027"]) == 0
    config = json.loads(capsys.readouterr().out)
    assert set(config["mcpServers"]) == {"openrouter-sidecar"}
    for entry in config["mcpServers"].values():
        assert entry["command"] == sys.executable
        assert entry["args"] == ["-m", "openrouter_image_mcp.cli"]
        assert "git+https" not in json.dumps(entry)


def test_missing_cohort_is_explained(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_IMAGE_COHORT")
    assert cli.main(["status"]) == 1
    assert "OPENROUTER_IMAGE_COHORT" in capsys.readouterr().err


def test_status_signed_out_exit_code_1(memory_keyring, capsys):
    assert cli.main(["status"]) == 1
    assert "Not signed in. Run `openrouter-image-mcp login`." in capsys.readouterr().out


class FakeClient:
    info: ClassVar[dict] = {}
    error: Exception | None = None

    def __init__(self, timeout_s, **kwargs):
        pass

    async def key_info(self):
        if self.error:
            raise self.error
        return self.info

    async def aclose(self):
        pass


def test_status_signed_in_prints_usage(memory_keyring, monkeypatch, capsys):
    keystore.set_key("sk-or-test")
    FakeClient.error = None
    FakeClient.info = {"label": "my key", "usage_daily": 1.234, "usage_monthly": 5.6}
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "my key" in out
    assert "Usage today: $1.23 / month: $5.60" in out
    assert "sk-or-test" not in out


def test_status_openrouter_error_exit_1(memory_keyring, monkeypatch, capsys):
    keystore.set_key("sk-or-test")
    FakeClient.error = OpenRouterError("boom happened")
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["status"]) == 1
    assert "boom happened" in capsys.readouterr().out


def test_logout_deletes_key(memory_keyring, capsys):
    keystore.set_key("sk-or-test")
    assert cli.main(["logout"]) == 0
    assert keystore.get_key() is None
    out = capsys.readouterr().out
    assert "Signed out. To revoke the key on OpenRouter, delete it at https://openrouter.ai/settings/keys" in out


class FakeLogin:
    instances: ClassVar[list] = []
    final = LoginState.SIGNED_IN

    def __init__(self, client, settings, **kwargs):
        self.switch = None
        FakeLogin.instances.append(self)

    async def start(self, switch_account=False):
        self.switch = switch_account
        return {"state": "pending", "url": "https://openrouter.ai/auth?x=1", "message": "Open https://openrouter.ai/auth?x=1"}

    def status(self):
        return {"state": self.final.value, "message": "status message", "url": None}

    async def wait(self):
        return self.final


@pytest.fixture
def fake_login(monkeypatch):
    FakeLogin.instances = []
    FakeLogin.final = LoginState.SIGNED_IN
    monkeypatch.setattr(cli, "LoginManager", FakeLogin)
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)


def test_login_success(fake_login, capsys):
    assert cli.main(["login"]) == 0
    assert "https://openrouter.ai/auth?x=1" in capsys.readouterr().out
    assert FakeLogin.instances[0].switch is False


def test_login_switch_and_failure(fake_login, capsys):
    FakeLogin.final = LoginState.FAILED
    assert cli.main(["login", "--switch"]) == 1
    assert FakeLogin.instances[0].switch is True
    assert "status message" in capsys.readouterr().out


class BrokenKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("locked")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("locked")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("locked")


def test_keyring_error_is_explained(memory_keyring, capsys):
    keyring.set_keyring(BrokenKeyring())  # memory_keyring fixture restores afterwards
    assert cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "Couldn't access the OS credential store: locked" in err


def test_listener_bind_error_is_explained(memory_keyring, monkeypatch, capsys):
    def no_bind(*args, **kwargs):
        raise OSError("[WinError 10013] access forbidden")

    monkeypatch.setattr(auth, "_CallbackServer", no_bind)
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["login"]) == 1
    err = capsys.readouterr().err
    assert "Couldn't start the local sign-in listener" in err
    assert "10013" in err


def test_status_label_is_redacted(memory_keyring, monkeypatch, capsys):
    keystore.set_key("sk-or-test")
    FakeClient.error = None
    FakeClient.info = {"label": "sk-or-v1-abc...xyz", "usage_daily": 0, "usage_monthly": 0}
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "sk-or-v1-abc" not in out
    assert "Signed in: sk-or-***" in out


def test_login_ctrl_c_cancels(fake_login, monkeypatch, capsys):
    cancelled = []

    async def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeLogin, "wait", interrupted)
    monkeypatch.setattr(FakeLogin, "cancel", lambda self: cancelled.append(self), raising=False)
    assert cli.main(["login"]) == 130
    assert cancelled == FakeLogin.instances
    assert capsys.readouterr().out.rstrip().endswith("Sign-in cancelled.")


def test_export_local_writes_a_private_bundle(tmp_path, monkeypatch, memory_keyring, capsys):
    from openrouter_image_mcp.advisor_store import AdvisorStore, default_store_path
    from openrouter_image_mcp.config import COHORT_WORKSPACES
    from openrouter_image_mcp.credentials import KeyringCredentials

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    workspace = COHORT_WORKSPACES["2027"]
    keystore.set_key("sk-or-v1-alice", workspace_id=workspace)
    _, scope = KeyringCredentials(workspace).resolve()
    AdvisorStore(default_store_path()).create(scope, "openai/gpt-test", "Critic")
    out = tmp_path / "export.json"
    assert cli.main(["export-local", "--cohort", "2027", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["chats"][0]["title"] == "Critic"
    assert "Exported 1 chat" in capsys.readouterr().out
    if sys.platform != "win32":
        assert out.stat().st_mode & 0o777 == 0o600


def test_export_local_needs_this_cohorts_sign_in(tmp_path, monkeypatch, memory_keyring, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert cli.main(["export-local", "--cohort", "2028", "--out", str(tmp_path / "x.json")]) == 1
    assert "login" in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()
