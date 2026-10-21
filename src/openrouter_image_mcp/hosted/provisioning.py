"""Give each signed-in student an OpenRouter key on first use, exactly once.

`KeyProvisioner.ensure` creates a key with the management API the first time a principal
needs one and stores it (`student_keys.py`). `provision_keys` is MCP middleware that runs
it before tool calls, because credentials resolve synchronously, even in background jobs.
When no key can be provided, the reason is kept for this call in a context variable;
`HostedCredentials.resolve()` raises it only if the tool actually needs the key.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass

from .. import storage
from ..config import COHORT_WORKSPACES
from ..errors import (
    KEY_REJECTED,
    AuthRequiredError,
    BadRequestError,
    NoGuardrailError,
    OpenRouterError,
    ProviderError,
)
from .openrouter_keys import TEAM_GUARDRAIL, GuardrailMissingError
from .principal import SLACK_ISSUER, Principal, current_principal
from .student_keys import StudentKey, StudentKeyStore

log = logging.getLogger(__name__)

NOT_SET_UP = "The Sidecar isn't set up to issue OpenRouter keys yet. Ask your instructor."
NOT_READY = "Your Sidecar OpenRouter key isn't ready yet. Try again in a moment."
NOT_SERVED = "Your class workspace is not served by this Sidecar; ask your instructor."
DISABLED = "Your Sidecar OpenRouter key was disabled by your instructor. Ask them to re-enable it."
UNAVAILABLE = ("Couldn't create your Sidecar OpenRouter key right now; try again in a minute. "
               "If it keeps happening, tell your instructor.")
# Tools that never read OpenRouter credentials; account_status provisions by itself.
NO_KEY_TOOLS = frozenset({"account_status", "create_image_upload", "get_image_download",
                          "open_image_uploader", "create_local_import"})

_pending: ContextVar[str | None] = ContextVar("sidecar_key_pending", default=None)


def pending_reason() -> str | None:
    """Why this call has no key (set by the middleware), or None."""
    return _pending.get()


@dataclass(frozen=True)
class TeamKey:
    """Marks a Slack team's key: named after the team and budgeted by `TEAM_GUARDRAIL`."""

    label: str


def key_name(principal: Principal, cohort: str | None = None, team_label: str | None = None) -> str:
    who = team_label or principal.email or "user-" + hashlib.sha256(principal.key.encode()).hexdigest()[:10]
    return f"Sidecar · {who} · {principal.cohort or cohort or '?'}"


def _cohort(workspace_id: str) -> str | None:
    return next((year for year, ws in COHORT_WORKSPACES.items() if ws == workspace_id), None)


class KeyProvisioner:
    def __init__(self, store: StudentKeyStore, manager, workspaces: set[str], *,
                 team_workspaces: frozenset[str] = frozenset()):
        """`workspaces` are the students' (cohort) workspaces; `team_workspaces` hold Slack team keys."""
        self.store, self.manager, self.workspaces = store, manager, set(workspaces)
        self.team_workspaces = frozenset(team_workspaces)
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, principal: Principal) -> asyncio.Lock:
        return self._locks.setdefault((principal.key, principal.workspace_id), asyncio.Lock())

    async def _row(self, principal: Principal) -> StudentKey | None:
        return await asyncio.to_thread(self.store.row, principal.key, principal.workspace_id)

    async def ensure(self, principal: Principal, *, team: TeamKey | None = None) -> StudentKey:
        """The principal's usable key row, creating the key at OpenRouter if needed.

        Slack team principals must pass `team` and students must not, so a student key can
        never be created under a team name or guardrail, nor a team key under a student's. Each kind is
        served only in its own workspaces, so a student can't get a key in the Slack workspace nor a team in a
        cohort's.
        """
        if (principal.issuer == SLACK_ISSUER) != (team is not None):
            raise BadRequestError("Team keys are only for Slack teams, and Slack teams need a team key.")
        if principal.workspace_id not in (self.team_workspaces if team else self.workspaces):
            raise AuthRequiredError(NOT_SERVED)
        row = await self._row(principal)
        if row is not None and row.rejected_at is None:
            return row
        # One creation per principal on this replica; the table's primary key covers others.
        async with self._lock(principal):
            row = await self._row(principal)
            if row is not None and row.rejected_at is not None:
                row = await self._recheck(principal, row)
            if row is not None:
                return row
            if self.manager is None:
                raise AuthRequiredError(NOT_SET_UP)
            return await self._create(principal, team)

    async def _create(self, principal: Principal, team: TeamKey | None = None) -> StudentKey:
        name = key_name(principal, _cohort(principal.workspace_id), team.label if team else None)
        extra = {"guardrail": TEAM_GUARDRAIL} if team else {}
        try:
            created = await self.manager.create(name, principal.workspace_id, **extra)
        except ProviderError as exc:
            log.warning("student key creation failed: %s", exc.message)
            if isinstance(exc, GuardrailMissingError):
                raise NoGuardrailError(UNAVAILABLE) from None
            raise ProviderError(UNAVAILABLE) from None
        try:
            stored = await asyncio.to_thread(self.store.insert, principal, created.key, created.hash, name)
        except Exception:
            await self._discard(created.hash)
            raise
        if not stored:  # another replica stored a key first; keep theirs
            await self._discard(created.hash)
        return await self._row(principal)

    async def _discard(self, key_hash: str) -> None:
        try:
            await self.manager.delete(key_hash)
            log.info("deleted unused student key %s…", key_hash[:8])
        except OpenRouterError as exc:
            log.warning("could not delete unused student key %s…: %s", key_hash[:8], exc.message)

    async def _recheck(self, principal: Principal, row: StudentKey) -> StudentKey | None:
        """After a 401: replace a deleted key, report a disabled one, clear a healthy one."""
        if self.manager is None:
            raise AuthRequiredError(KEY_REJECTED)
        try:
            data = await self.manager.get(row.key_hash)
        except ProviderError as exc:
            log.warning("student key recheck failed: %s", exc.message)
            raise AuthRequiredError(KEY_REJECTED) from None
        if data is None:
            await asyncio.to_thread(self.store.remove, row.principal, row.workspace, row.key_hash)
            return None
        if data.get("disabled"):
            raise AuthRequiredError(DISABLED)
        await asyncio.to_thread(self.store.clear_rejected, row.principal, row.workspace)
        return await self._row(principal)

    async def prepare(self, principal: Principal) -> None:
        """Provision before a tool runs; remember why not, instead of failing free tools."""
        try:
            await self.ensure(principal)
        except OpenRouterError as exc:
            _pending.set(exc.message)
        except storage.DB_ERRORS as exc:  # logged; the tool then says "not ready"
            log.warning("student key provisioning failed: %s", type(exc).__name__)
            _pending.set(NOT_READY)
        else:
            _pending.set(None)


def key_middleware(provisioner: KeyProvisioner, profile_claim: str, gate):
    """MCP middleware: provision the caller's key before a tool call that may need it."""
    async def provision_keys(ctx, call_next):
        name = (ctx.params or {}).get("name")
        if ctx.method == "tools/call" and name not in NO_KEY_TOOLS and gate.reason is None:
            try:
                principal = current_principal(profile_claim)
            except AuthRequiredError:
                principal = None  # the tool reports sign-in problems itself
            if principal is not None and principal.workspace_id in provisioner.workspaces:
                await provisioner.prepare(principal)
        return await call_next(ctx)
    return provision_keys
