import pytest

from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.verifier import Auth0Verifier
from tests import jwt_helpers as h


@pytest.fixture
def verifier():
    return Auth0Verifier(issuer=h.ISSUER, resource=h.RESOURCE, key_resolver=h.resolver)


async def test_valid_token_maps_permissions_to_scopes(verifier):
    access = await verifier.verify_token(h.token())
    assert access.scopes == ["sidecar:use"]
    assert access.resource == h.RESOURCE
    assert access.subject == "email|alice"
    assert access.client_id == "client-1"
    principal = Principal.from_access_token(access, h.PROFILE)
    assert principal.key == "https://tenant.us.auth0.com/|email|alice"
    assert principal.workspace_id == h.WORKSPACE
    assert principal.cohort == "2027"


@pytest.mark.parametrize("overrides", [
    {"iss": "https://evil.example/"},
    {"aud": "https://other.example/mcp"},
    {"exp": 1},
    {"sub": None},
    {"iat": None},
])
async def test_rejects_bad_claims(verifier, overrides):
    assert await verifier.verify_token(h.token(**overrides)) is None


async def test_rejects_wrong_signature(verifier):
    assert await verifier.verify_token(h.token(key=h.OTHER_KEY)) is None


async def test_rejects_hs256_confusion(verifier):
    forged = h.token(key="secret" * 8, alg="HS256")
    assert await verifier.verify_token(forged) is None


async def test_rejects_garbage(verifier):
    assert await verifier.verify_token("not-a-jwt") is None


async def test_resolver_failure_is_a_rejection():
    def broken(_token):
        raise RuntimeError("jwks unavailable")

    verifier = Auth0Verifier(issuer=h.ISSUER, resource=h.RESOURCE, key_resolver=broken)
    assert await verifier.verify_token(h.token()) is None


async def test_missing_workspace_fails_closed(verifier):
    access = await verifier.verify_token(h.token(**{h.PROFILE: {"email": "x@y"}}))
    with pytest.raises(PermissionError, match="workspace"):
        Principal.from_access_token(access, h.PROFILE)


async def test_missing_profile_fails_closed(verifier):
    access = await verifier.verify_token(h.token(**{h.PROFILE: None}))
    with pytest.raises(PermissionError):
        Principal.from_access_token(access, h.PROFILE)


async def test_missing_permissions_yields_empty_scopes(verifier):
    access = await verifier.verify_token(h.token(permissions=None))
    assert access.scopes == []  # the SDK's required_scopes then answers 403


async def test_token_is_never_logged(verifier, caplog):
    bad = h.token(key=h.OTHER_KEY)
    await verifier.verify_token(bad)
    assert bad not in caplog.text


@pytest.mark.parametrize("profile", [None, {"email": "x@y"}, {"workspace_id": ""}, {"workspace_id": 7}])
async def test_profile_without_workspace_grants_no_scopes(profile):
    # The SDK's required_scopes then answers 403 before any tool runs.
    verifier = Auth0Verifier(issuer=h.ISSUER, resource=h.RESOURCE, key_resolver=h.resolver,
                             profile_claim=h.PROFILE)
    access = await verifier.verify_token(h.token(**{h.PROFILE: profile}))
    assert access is not None and access.scopes == []
    ok = await verifier.verify_token(h.token())
    assert ok.scopes == ["sidecar:use"]
