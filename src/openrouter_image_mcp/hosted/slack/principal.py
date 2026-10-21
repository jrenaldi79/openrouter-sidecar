"""The Slack team principal and its credential source.

A registered channel acts as one principal whose identity is built only from the signed
event's ids and the registry row, never from message text. Its issuer is `slack`, so its
key can never equal an Auth0 student's (`issuer|subject`). The principal travels in a
module-private context variable that `acting_as` sets and always resets; `SlackCredentials`
reads it the way `HostedCredentials` reads the request's token, so team data and student
data never mix.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ...advisor_store import AdvisorAccessError, Scope
from ...credentials import CHANGED
from ...errors import AuthRequiredError
from ..principal import SLACK_ISSUER, Principal
from ..provisioning import NOT_READY, pending_reason
from ..student_keys import StudentKeyStore
from .registry import ChannelRow

__all__ = ["SLACK_ISSUER", "SlackCredentials", "acting_as", "team_principal"]

NO_CHANNEL = "No Slack channel in context."

_acting: ContextVar[Principal | None] = ContextVar("sidecar_slack_principal", default=None)


def team_principal(team_id: str, channel_id: str, row: ChannelRow) -> Principal:
    """The principal of a registered, active channel; `ValueError` for anything else."""
    if (team_id, channel_id) != (row.slack_team_id, row.channel_id):
        raise ValueError("The Slack ids do not match the registered channel.")
    if not row.active:
        raise ValueError("This Slack channel is not active.")
    return Principal(SLACK_ISSUER, f"{team_id}:{channel_id}", row.workspace_id, row.cohort)


@contextmanager
def acting_as(principal: Principal) -> Iterator[None]:
    """Run the enclosed code, and tasks it starts, as this team."""
    token = _acting.set(principal)
    try:
        yield
    finally:
        _acting.reset(token)


def _current() -> Principal:
    principal = _acting.get()
    if principal is None:
        raise AuthRequiredError(NO_CHANNEL)
    return principal


class SlackCredentials:
    """`CredentialSource` for the team in context; one instance, for the Slack workspace."""

    def __init__(self, workspace_id: str, keys: StudentKeyStore):
        self.workspace_id, self.keys = workspace_id, keys

    def resolve(self) -> tuple[str, Scope]:
        principal = _current()
        if principal.workspace_id != self.workspace_id:
            raise AdvisorAccessError("This advisor belongs to a different class workspace.")
        key = self.keys.key_for(principal)
        if key is None:
            raise AuthRequiredError(pending_reason() or NOT_READY)
        return key, Scope(principal.key, principal.workspace_id)

    def check(self, scope: Scope) -> None:
        # Never read the context here: heartbeats run outside it.
        if scope.workspace != self.workspace_id or not self.keys.is_active(scope.owner, scope.workspace):
            raise AdvisorAccessError(CHANGED)

    def forget(self) -> None:
        """OpenRouter answered 401: mark the team's key rejected; the next call rechecks it."""
        principal = _current()
        self.keys.mark_rejected(principal.key, principal.workspace_id)
