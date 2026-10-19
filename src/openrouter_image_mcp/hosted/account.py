"""`account_status`: the caller's sign-in, cohort, provisioned OpenRouter key and budget."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from ..errors import AuthRequiredError, OpenRouterError
from .openrouter_keys import current_key
from .principal import Principal
from .provisioning import KeyProvisioner


def _money(value) -> str:
    return f"${float(value):.2f}" if isinstance(value, int | float) else "?"


def _date(value) -> str:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC).date().isoformat()
    return str(value)[:10] if value else "?"


def usage_lines(data: dict) -> list[str]:
    """Usage this month and the budget OpenRouter enforces first, from `GET /key`."""
    lines = [f"Spent this month: {_money(data.get('usage_monthly'))}."]
    budget = data.get("effective_budget")
    if isinstance(budget, dict) and budget.get("limit_usd") is not None:
        period = budget.get("reset_interval") or "lifetime"
        resets = f", resets {_date(budget['resets_at'])}" if budget.get("resets_at") else ""
        lines.append(f"Budget: {_money(budget.get('spend_usd'))} of {_money(budget['limit_usd'])} "
                     f"used ({period}{resets}; set by your class workspace).")
    elif isinstance(data.get("limit"), int | float):
        lines.append(f"Key limit: {_money(data.get('limit'))}, {_money(data.get('limit_remaining'))} left.")
    return lines


async def key_report(provisioner: KeyProvisioner, who: Principal, transport=None) -> list[str]:
    """Provision if needed, then describe the key. Never includes the key itself."""
    try:
        row = await provisioner.ensure(who)
        key = await asyncio.to_thread(provisioner.store.key_for, who)
        try:
            data = await current_key(key, transport=transport)
        except AuthRequiredError:
            # Rejected (disabled or deleted): mark it, then let ensure() decide what it means.
            await asyncio.to_thread(provisioner.store.mark_rejected, who.key, who.workspace_id)
            row = await provisioner.ensure(who)
            key = await asyncio.to_thread(provisioner.store.key_for, who)
            data = await current_key(key, transport=transport)
    except OpenRouterError as exc:
        return [f"OpenRouter key: not available. {exc.message}"]
    issued = (f"OpenRouter key: {row.key_name} (issued {_date(row.created_at)}, billed to your "
              "class workspace; no OpenRouter account needed).")
    return [issued, *usage_lines(data)]


def register_account_tools(server: MCPServer, provisioner: KeyProvisioner, principal, *,
                           transport=None, tool_errors) -> None:
    """principal() checks the admission gate and returns the caller's Principal."""

    async def account_status() -> str:
        """Show your Sidecar sign-in, class cohort, OpenRouter key and spend this month.

        No inference and no cost. The first call issues your class OpenRouter key if
        you don't have one yet; you never need your own OpenRouter account.
        """
        with tool_errors():
            who = principal()
            lines = [f"Signed in as {who.email or who.subject}. Class of {who.cohort or '?'}."]
            return "\n".join(lines + await key_report(provisioner, who, transport))

    server.add_tool(account_status, description=inspect.cleandoc(account_status.__doc__),
                    structured_output=False)
