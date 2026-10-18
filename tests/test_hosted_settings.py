import pytest

from openrouter_image_mcp.hosted.settings import load_hosted_settings

BASE = {
    "AUTH0_ISSUER": "https://tenant.us.auth0.com/",
    "MCP_RESOURCE_URI": "https://sidecar.example/mcp",
    "DATABASE_URL": "postgresql://app@db.railway.internal/sidecar",
    "VAULT_KEYS": "v1:" + "A" * 43 + "=",
    "LINK_SIGNING_KEY": "x" * 32,
}


def test_loads_required_values():
    s = load_hosted_settings(BASE)
    assert s.issuer == "https://tenant.us.auth0.com/"
    assert s.jwks_uri == "https://tenant.us.auth0.com/.well-known/jwks.json"
    assert s.permission == "sidecar:use"
    assert s.profile_claim == "https://mpd.northwestern.edu/profile"
    assert s.public_origin == "https://sidecar.example"
    assert s.sandbox_enabled is False


@pytest.mark.parametrize("key,value", [
    ("AUTH0_ISSUER", "https://tenant.us.auth0.com"),      # missing trailing slash
    ("AUTH0_ISSUER", "http://tenant.us.auth0.com/"),      # not https
    ("MCP_RESOURCE_URI", "https://sidecar.example/mcp/"),  # must end exactly /mcp
    ("MCP_RESOURCE_URI", "http://sidecar.example/mcp"),    # http only for localhost
    ("LINK_SIGNING_KEY", "short"),
])
def test_rejects_unsafe_values(key, value):
    with pytest.raises(ValueError):
        load_hosted_settings(BASE | {key: value})


@pytest.mark.parametrize("missing", sorted(BASE))
def test_requires_every_value(missing):
    env = dict(BASE)
    del env[missing]
    with pytest.raises(ValueError, match=missing):
        load_hosted_settings(env)


DEV = {"MCP_RESOURCE_URI": "http://localhost:8000/mcp", "AUTH0_ISSUER": "http://127.0.0.1:9400/",
       "SIDECAR_DEV_INSECURE_ISSUER": "1"}


def test_localhost_resource_and_issuer_allowed_for_development():
    s = load_hosted_settings(BASE | DEV)
    assert s.public_origin == "http://localhost:8000"
    assert s.jwks_uri == "http://127.0.0.1:9400/.well-known/jwks.json"


def test_http_issuer_needs_explicit_dev_flag():
    with pytest.raises(ValueError, match="SIDECAR_DEV_INSECURE_ISSUER"):
        load_hosted_settings(BASE | DEV | {"SIDECAR_DEV_INSECURE_ISSUER": ""})


def test_dev_issuer_refused_for_public_resource():
    with pytest.raises(ValueError):
        load_hosted_settings(BASE | DEV | {"MCP_RESOURCE_URI": "https://sidecar.example/mcp"})


def test_http_resource_on_localhost_allowed_with_https_issuer():
    s = load_hosted_settings(BASE | {"MCP_RESOURCE_URI": "http://localhost:8000/mcp"})
    assert s.issuer.startswith("https://")


def test_optional_overrides():
    s = load_hosted_settings(BASE | {"MCP_PERMISSION": "sidecar:admin",
                                     "SIDECAR_SANDBOX_ENABLED": "true",
                                     "AUTH0_JWKS_URI": "https://keys.example/jwks.json"})
    assert s.permission == "sidecar:admin"
    assert s.sandbox_enabled is True
    assert s.jwks_uri == "https://keys.example/jwks.json"


def test_dev_openrouter_url_only_for_local_development():
    s = load_hosted_settings(BASE | DEV | {"SIDECAR_DEV_OPENROUTER_URL": "http://127.0.0.1:9500"})
    assert s.dev_openrouter_url == "http://127.0.0.1:9500"
    with pytest.raises(ValueError, match="SIDECAR_DEV_OPENROUTER_URL"):
        load_hosted_settings(BASE | {"SIDECAR_DEV_OPENROUTER_URL": "http://127.0.0.1:9500"})
    with pytest.raises(ValueError, match="SIDECAR_DEV_OPENROUTER_URL"):
        load_hosted_settings(BASE | DEV | {"SIDECAR_DEV_OPENROUTER_URL": "https://evil.example"})
