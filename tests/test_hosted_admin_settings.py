import pytest

from openrouter_image_mcp.hosted.admin.settings import (
    AdminSettings,
    load_admin_settings,
)
from openrouter_image_mcp.hosted.settings import load_hosted_settings

ISSUER = "https://tenant.us.auth0.com/"
RESOURCE = "https://sidecar.example/mcp"
ORIGIN = "https://sidecar.example"
PROFILE = "https://mpd.northwestern.edu/profile"
BASE = {
    "AUTH0_ISSUER": ISSUER,
    "MCP_RESOURCE_URI": RESOURCE,
    "DATABASE_URL": "postgresql://app@db.railway.internal/sidecar",
    "VAULT_KEYS": "v1:" + "A" * 43 + "=",
    "LINK_SIGNING_KEY": "x" * 32,
}


def load(env=None, origin=ORIGIN) -> AdminSettings:
    return load_admin_settings(env or {}, issuer=ISSUER, resource=RESOURCE, public_origin=origin,
                               profile_claim=PROFILE)


def test_on_by_default_and_not_configured():
    s = load()
    assert s.enabled is True
    assert s.configured is False
    assert s.missing() == ["SIDECAR_ADMIN_CLIENT_ID", "SIDECAR_ADMIN_CLIENT_SECRET"]
    assert (s.issuer, s.resource, s.public_origin) == (ISSUER, RESOURCE, ORIGIN)


def test_configured_when_both_values_are_set():
    s = load({"SIDECAR_ADMIN_CLIENT_ID": "client-id", "SIDECAR_ADMIN_CLIENT_SECRET": "s" * 40})
    assert s.configured is True
    assert s.missing() == []
    assert s.client_id == "client-id"
    assert s.client_secret == "s" * 40


def test_one_missing_value_is_named():
    assert load({"SIDECAR_ADMIN_CLIENT_ID": "client-id"}).missing() == ["SIDECAR_ADMIN_CLIENT_SECRET"]
    assert load({"SIDECAR_ADMIN_CLIENT_SECRET": "s" * 40}).missing() == ["SIDECAR_ADMIN_CLIENT_ID"]


@pytest.mark.parametrize("value", ["false", "0", "no", "FALSE", "No", " false "])
def test_disabled_values(value):
    assert load({"SIDECAR_ADMIN_ENABLED": value}).enabled is False


@pytest.mark.parametrize("value", ["", "true", "1", "yes", "anything"])
def test_other_values_leave_it_on(value):
    assert load({"SIDECAR_ADMIN_ENABLED": value}).enabled is True


def test_short_secret_stops_startup():
    with pytest.raises(ValueError, match="SIDECAR_ADMIN_CLIENT_SECRET"):
        load({"SIDECAR_ADMIN_CLIENT_SECRET": "s" * 31})
    assert load({"SIDECAR_ADMIN_CLIENT_SECRET": "s" * 32}).client_secret == "s" * 32


def test_urls_and_secure_cookies():
    s = load()
    assert s.callback_url == "https://sidecar.example/admin/callback"
    assert s.logout_return == "https://sidecar.example/admin"
    assert s.secure_cookies is True
    local = load(origin="http://localhost:8000")
    assert local.callback_url == "http://localhost:8000/admin/callback"
    assert local.logout_return == "http://localhost:8000/admin"
    assert local.secure_cookies is False
    assert load(origin="http://127.0.0.1:8000").secure_cookies is False
    assert load(origin="https://localhost:8000").secure_cookies is True
    assert load(origin="http://sidecar.example").secure_cookies is True


def test_secret_not_in_repr():
    secret = "s" * 40
    s = load({"SIDECAR_ADMIN_CLIENT_ID": "client-id", "SIDECAR_ADMIN_CLIENT_SECRET": secret})
    assert secret not in repr(s)
    assert "client-id" in repr(s)


def test_hosted_settings_carry_admin():
    s = load_hosted_settings(BASE | {"SIDECAR_ADMIN_CLIENT_ID": "client-id",
                                     "SIDECAR_ADMIN_CLIENT_SECRET": "s" * 40})
    assert s.admin is not None
    assert s.admin.enabled and s.admin.configured
    assert s.admin.callback_url == "https://sidecar.example/admin/callback"
    assert (s.admin.issuer, s.admin.resource) == (s.issuer, s.resource)


def test_admin_profile_claim_follows_hosted_setting():
    assert load_hosted_settings(BASE).admin.profile_claim == PROFILE
    custom = load_hosted_settings(BASE | {"PROFILE_CLAIM": "https://other.example/profile"})
    assert custom.admin.profile_claim == custom.profile_claim == "https://other.example/profile"


def test_hosted_settings_reject_short_admin_secret():
    with pytest.raises(ValueError):
        load_hosted_settings(BASE | {"SIDECAR_ADMIN_CLIENT_SECRET": "s" * 31})
