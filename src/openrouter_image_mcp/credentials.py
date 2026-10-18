"""Where the OpenRouter key and ownership scope come from for the current call."""

from __future__ import annotations

import hashlib
from typing import Protocol

from . import keystore
from .advisor_store import AdvisorAccessError, Scope
from .errors import AuthRequiredError

SIGN_IN = "Not signed in. Call auth_login before using an advisor."
CHANGED = "Advisor sign-in changed during the operation; reply withheld."


class CredentialSource(Protocol):
    workspace_id: str

    def resolve(self) -> tuple[str, Scope]: ...
    def check(self, scope: Scope) -> None: ...
    def forget(self) -> None: ...


class KeyringCredentials:
    """Local stdio mode: one OS-keyring credential per cohort workspace."""

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id

    def resolve(self) -> tuple[str, Scope]:
        key = keystore.get_key(workspace_id=self.workspace_id)
        if not key:
            raise AuthRequiredError(SIGN_IN)
        # Scope tracks this credential, not a provider-verified student identity.
        return key, Scope(hashlib.sha256(key.encode()).hexdigest(), self.workspace_id)

    def check(self, scope: Scope) -> None:
        key = keystore.get_key(workspace_id=self.workspace_id)
        if (scope.workspace != self.workspace_id or not key
                or hashlib.sha256(key.encode()).hexdigest() != scope.owner):
            raise AdvisorAccessError(CHANGED)

    def forget(self) -> None:
        keystore.delete_key(workspace_id=self.workspace_id)
