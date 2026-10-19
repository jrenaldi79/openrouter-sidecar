"""Read-only access to the key the retired local Sidecar left in the OS keyring.

`export-local` needs it to find that student's local chats (their owner scope is the
key's SHA-256). Nothing writes to the keyring any more, and there is no plaintext fallback.
"""

import hashlib

import keyring
import keyring.backend
import keyring.backends.fail
import keyring.backends.null
import pytest

from openrouter_image_mcp import keystore
from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.config import COHORT_WORKSPACES, KEYRING_SERVICE

WS = COHORT_WORKSPACES["2027"]


class PlaintextKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        return None

    def set_password(self, service, username, password):
        pass

    def delete_password(self, service, username):
        pass


def test_reads_the_key_stored_per_cohort_workspace(memory_keyring):
    assert keystore.get_key(workspace_id=WS) is None
    memory_keyring.set_password(KEYRING_SERVICE, WS, "sk-or-v1-secret")
    assert keystore.get_key(workspace_id=WS) == "sk-or-v1-secret"
    assert keystore.get_key(workspace_id=COHORT_WORKSPACES["2028"]) is None


def test_local_scope_is_the_key_hash_in_its_workspace(memory_keyring):
    assert keystore.local_scope(WS) is None
    memory_keyring.set_password(KEYRING_SERVICE, WS, "sk-test")
    assert keystore.local_scope(WS) == Scope(hashlib.sha256(b"sk-test").hexdigest(), WS)


def test_the_module_cannot_write_or_delete_keys():
    assert not hasattr(keystore, "set_key") and not hasattr(keystore, "delete_key")


@pytest.mark.parametrize(
    "backend",
    [keyring.backends.fail.Keyring(), keyring.backends.null.Keyring(), PlaintextKeyring()],
    ids=["fail", "null", "plaintext"],
)
def test_refuses_insecure_backend(memory_keyring, backend):
    keyring.set_keyring(backend)  # memory_keyring fixture restores afterwards
    with pytest.raises(keystore.InsecureKeyringError, match="no plaintext fallback"):
        keystore.get_key(workspace_id=WS)


def test_refuses_insecure_backend_inside_chainer(memory_keyring):
    from keyring.backends import chainer

    class FakeChain(chainer.ChainerBackend):
        @property
        def backends(self):
            return [memory_keyring, PlaintextKeyring()]

    keyring.set_keyring(FakeChain())
    with pytest.raises(keystore.InsecureKeyringError):
        keystore.get_key(workspace_id=WS)


def test_accepts_chainer_of_secure_backends(memory_keyring):
    from keyring.backends import chainer

    other = type(memory_keyring)()

    class FakeChain(chainer.ChainerBackend):
        @property
        def backends(self):
            return [memory_keyring, other]

    memory_keyring.set_password(KEYRING_SERVICE, WS, "sk-or-v1-secret")
    keyring.set_keyring(FakeChain())
    assert keystore.get_key(workspace_id=WS) == "sk-or-v1-secret"
