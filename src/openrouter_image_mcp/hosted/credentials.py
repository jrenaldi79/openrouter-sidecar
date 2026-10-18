"""Hosted credential source: the request's Auth0 principal plus its linked OpenRouter key."""

from __future__ import annotations

from ..advisor_store import AdvisorAccessError, Scope
from ..credentials import CHANGED
from .links import LinkStore
from .principal import current_principal


class HostedCredentials:
    """One instance per cohort workspace; the hosted server routes callers to theirs."""

    def __init__(self, workspace_id: str, links: LinkStore, profile_claim: str):
        self.workspace_id, self.links, self.profile_claim = workspace_id, links, profile_claim

    def resolve(self) -> tuple[str, Scope]:
        # Background jobs inherit the request's context variables, so this also
        # works inside tasks started by a tool call.
        principal = current_principal(self.profile_claim)
        if principal.workspace_id != self.workspace_id:
            raise AdvisorAccessError("This advisor belongs to a different class workspace.")
        key = self.links.key_for(principal)
        return key, Scope(principal.key, principal.workspace_id)

    def check(self, scope: Scope) -> None:
        # Never read the request context here: heartbeats run outside it. A
        # revoked or replaced link withholds in-flight results.
        if scope.workspace != self.workspace_id or not self.links.is_active(scope.owner, scope.workspace):
            raise AdvisorAccessError(CHANGED)

    def forget(self) -> None:
        principal = current_principal(self.profile_claim)
        self.links.revoke(principal.key, principal.workspace_id)
