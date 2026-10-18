"""Encrypt OpenRouter keys at rest. Associated data binds a ciphertext to its principal."""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode(encoded: str) -> bytes:
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return b""


class Vault:
    """VAULT_KEYS = 'v2:<b64>,v1:<b64>'; the first entry encrypts, all entries decrypt."""

    def __init__(self, spec: str):
        self._keys: dict[str, AESGCM] = {}
        self._current = None
        for item in filter(None, (part.strip() for part in spec.split(","))):
            version, sep, encoded = item.partition(":")
            raw = _decode(encoded) if sep else b""
            if not version or len(raw) != 32:
                raise ValueError("VAULT_KEYS entries must be '<version>:<base64 32-byte key>'.")
            self._keys[version] = AESGCM(raw)
            self._current = self._current or version
        if not self._current:
            raise ValueError("VAULT_KEYS is empty.")

    def seal(self, secret: str, *, principal_key: str) -> str:
        nonce = os.urandom(12)
        data = self._keys[self._current].encrypt(nonce, secret.encode(), principal_key.encode())
        return self._current + ":" + base64.b64encode(nonce + data).decode()

    def open(self, sealed: str, *, principal_key: str) -> str:
        version, _, encoded = sealed.partition(":")
        if version not in self._keys:
            raise ValueError("Unknown vault key version.")
        blob = _decode(encoded)
        try:
            return self._keys[version].decrypt(blob[:12], blob[12:], principal_key.encode()).decode()
        except (InvalidTag, ValueError):
            raise ValueError("Vault entry does not belong to this principal.") from None

    def needs_rotation(self, sealed: str) -> bool:
        return not sealed.startswith(self._current + ":")
