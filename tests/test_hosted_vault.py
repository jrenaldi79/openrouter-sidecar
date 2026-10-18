import base64
import os

import pytest

from openrouter_image_mcp.hosted.vault import Vault

K1 = "v1:" + base64.b64encode(os.urandom(32)).decode()
K2 = "v2:" + base64.b64encode(os.urandom(32)).decode()


def test_round_trip_bound_to_principal():
    vault = Vault(K1)
    sealed = vault.seal("sk-or-v1-secret", principal_key="iss|sub")
    assert "sk-or" not in sealed and sealed.startswith("v1:")
    assert vault.open(sealed, principal_key="iss|sub") == "sk-or-v1-secret"


def test_each_seal_uses_a_fresh_nonce():
    vault = Vault(K1)
    assert vault.seal("s", principal_key="p") != vault.seal("s", principal_key="p")


def test_wrong_principal_cannot_open():
    vault = Vault(K1)
    sealed = vault.seal("secret", principal_key="iss|a")
    with pytest.raises(ValueError):
        vault.open(sealed, principal_key="iss|b")


def test_tampered_ciphertext_rejected():
    vault = Vault(K1)
    sealed = vault.seal("secret", principal_key="p")
    blob = bytearray(base64.b64decode(sealed.split(":", 1)[1]))
    blob[-1] ^= 1
    with pytest.raises(ValueError):
        vault.open("v1:" + base64.b64encode(bytes(blob)).decode(), principal_key="p")


def test_rotation_reads_old_writes_new():
    old = Vault(K1).seal("secret", principal_key="p")
    rotated = Vault(K2 + "," + K1)  # first key is current
    assert rotated.open(old, principal_key="p") == "secret"
    assert rotated.seal("secret", principal_key="p").startswith("v2:")
    assert rotated.needs_rotation(old)


def test_unknown_version_rejected():
    sealed = Vault(K2).seal("secret", principal_key="p")
    with pytest.raises(ValueError):
        Vault(K1).open(sealed, principal_key="p")


@pytest.mark.parametrize("bad", ["", "v1:short", "nocolon", "v1:!!!notbase64"])
def test_rejects_bad_key_config(bad):
    with pytest.raises(ValueError):
        Vault(bad)
