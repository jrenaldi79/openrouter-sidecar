"""Read the key the retired local Sidecar left in the OS keyring, for `export-local` only.

The local server stored one OpenRouter key per cohort workspace and owned its chats
by the key's SHA-256. The export needs that owner scope to find them. Nothing here
writes or deletes a key, and an insecure backend is refused (no plaintext fallback).
"""

import hashlib

import keyring
from keyring.backends import chainer

from .advisor_store import Scope
from .config import KEYRING_SERVICE

_INSECURE_MODULES = {"keyring.backends.fail", "keyring.backends.null"}


class InsecureKeyringError(RuntimeError):
    """Raised when the active keyring backend cannot store secrets securely."""


def _is_insecure(backend) -> bool:
    cls = type(backend)
    return cls.__module__ in _INSECURE_MODULES or "Plaintext" in cls.__name__


def _check_backend() -> None:
    backend = keyring.get_keyring()
    if isinstance(backend, chainer.ChainerBackend):
        backends = list(backend.backends)
        insecure = not backends or any(_is_insecure(b) for b in backends)
    else:
        insecure = _is_insecure(backend)
    if insecure:
        raise InsecureKeyringError(
            "No secure OS keyring is available, and there is no plaintext fallback. "
            "Set up a system keyring (Windows Credential Manager, macOS Keychain, "
            "or Secret Service) and try again."
        )


def get_key(*, workspace_id: str) -> str | None:
    _check_backend()
    return keyring.get_password(KEYRING_SERVICE, workspace_id)


def local_scope(workspace_id: str) -> Scope | None:
    """The local chats' owner scope for this cohort, or None if it never signed in here."""
    key = get_key(workspace_id=workspace_id)
    return Scope(hashlib.sha256(key.encode()).hexdigest(), workspace_id) if key else None
