"""Hosted credential source: the request's Auth0 principal plus its provisioned OpenRouter key."""

from __future__ import annotations

from ..advisor_store import AdvisorAccessError, Scope
from ..credentials import CHANGED
from ..errors import AuthRequiredError
from .principal import current_principal
from .provisioning import NOT_READY, pending_reason
from .student_keys import StudentKeyStore


class HostedCredentials:
    """One instance per cohort workspace; the hosted server routes callers to theirs.

    Keys are provisioned asynchronously before a tool runs (`provisioning.py`), so
    resolve() stays a fast store lookup that also works inside background jobs.
    """

    def __init__(self, workspace_id: str, keys: StudentKeyStore, profile_claim: str):
        self.workspace_id, self.keys, self.profile_claim = workspace_id, keys, profile_claim

    def resolve(self) -> tuple[str, Scope]:
        # Background jobs inherit the request's context variables, so this also
        # works inside tasks started by a tool call.
        principal = current_principal(self.profile_claim)
        if principal.workspace_id != self.workspace_id:
            raise AdvisorAccessError("This advisor belongs to a different class workspace.")
        key = self.keys.key_for(principal)
        if key is None:
            raise AuthRequiredError(pending_reason() or NOT_READY)
        return key, Scope(principal.key, principal.workspace_id)

    def check(self, scope: Scope) -> None:
        # Never read the request context here: heartbeats run outside it. A deleted
        # or rejected key withholds in-flight results.
        if scope.workspace != self.workspace_id or not self.keys.is_active(scope.owner, scope.workspace):
            raise AdvisorAccessError(CHANGED)

    def forget(self) -> None:
        """OpenRouter answered 401: mark the key rejected; the next call rechecks it."""
        principal = current_principal(self.profile_claim)
        self.keys.mark_rejected(principal.key, principal.workspace_id)
