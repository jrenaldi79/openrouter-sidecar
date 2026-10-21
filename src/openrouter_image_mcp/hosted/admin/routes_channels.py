"""The pages: Channels, the register and edit forms, and the change log. Reads only; saving is `routes_actions`."""

from __future__ import annotations

from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import Response

from ... import config
from ..slack.api import SlackCallError
from ..slack.channel_admin import ChannelAdmin
from ..slack.channel_setup import SETUP_REASONS, ChannelRefused
from . import copy, forms
from .routes_base import AdminContext, redirect

LOG_LIMIT = 200


async def index(ctx: AdminContext, request: Request, session, form) -> Response:
    overview = await ctx.channels.overview()
    return ctx.views.page("channels", session=session, overview=overview)


async def log_page(ctx: AdminContext, request: Request, session, form) -> Response:
    return ctx.views.page("log", session=session, entries=ctx.audit.recent(LOG_LIMIT))


async def slack_name(channels: ChannelAdmin, channel_id: str) -> str:
    """The #channel name, or the id when Slack is off or won't say."""
    if channels.api is None:
        return channel_id
    try:
        return (await channels.api.channel_info(channel_id)).get("name") or channel_id
    except SlackCallError:
        return channel_id


async def team_id(channels: ChannelAdmin) -> str:
    """The bot's Slack team, which is the `<team>` of every channel it registers."""
    if channels.api is None:
        raise ChannelRefused(copy.SLACK_OFF)
    try:
        return await channels.api.team_id()
    except SlackCallError:
        raise ChannelRefused(copy.SLACK_DOWN) from None


async def refusal_page(ctx: AdminContext, request: Request, session, build) -> Response:
    """Await a page builder; a refusal while gathering its data (model list, Slack) becomes a plain page."""
    try:
        return await build
    except ChannelRefused as refusal:
        return ctx.dead_end(request, str(refusal), 422, session)


def edit_url(team: str, channel_id: str) -> str:
    return f"/admin/channels/{quote(team, safe='')}/{quote(channel_id, safe='')}"


async def _choices(ctx: AdminContext, channel_id: str, *, keep_input: bool):
    """`(models, members, #name, list_down)` a form offers. Showing a refused submission again, or the edit page
    of a saved channel, must not fail because the model list is down now, so with `keep_input` a failed list is
    empty (the current and typed models stay selectable) and `list_down` is True."""
    down = False
    try:
        models = await ctx.channels.catalog()
    except ChannelRefused as refusal:
        if not keep_input or str(refusal) == copy.SLACK_OFF:  # with Slack off there is no model list to wait for
            raise
        models, down = [], True
    return models, await ctx.channels.members(channel_id), await slack_name(ctx.channels, channel_id), down


async def register_page(ctx: AdminContext, session, channel_id: str, *, shown: dict | None = None,
                        error: str | None = None, status: int = 200) -> Response:
    """The register form: blank, or showing a refused submission."""
    models, members, name, down = await _choices(ctx, channel_id, keep_input=shown is not None)
    values = shown or forms.blank_values(next(iter(config.COHORT_WORKSPACES)), *ctx.register_defaults)
    families = sorted({m["family"] for m in models} | set(values["family_models"]))
    values = {**values, "family_models": {f: values["family_models"].get(f, "") for f in families}}
    unsaved = {**values, "family_models": dict.fromkeys(families, "")}  # a new team has no saved models
    return ctx.views.page(
        "channel_form", session=session, status=status, mode="register", row=None,
        channel={"id": channel_id, "name": name}, values=values, orig=unsaved, models=models,
        families=families, aliases=list(values["alias_map"]), members=members, models_readable=True,
        error=error or (copy.CATALOG_DOWN if down else None), setup_unfinished=False)


async def edit_page(ctx: AdminContext, request: Request, session, team: str, channel_id: str, *,
                    shown: dict | None = None, orig: dict | None = None, error: str | None = None,
                    status: int = 200) -> Response:
    """The edit form for a registered channel. With `shown`, the inputs keep a refused submission while the
    hidden originals stay the snapshot it was rendered from."""
    found = await ctx.channels.channel(team, channel_id)
    if found is None:
        return ctx.dead_end(request, copy.NOT_REGISTERED, 404, session)
    row, saved = found
    models, members, name, down = await _choices(ctx, channel_id, keep_input=True)  # Turn off must stay reachable
    families = sorted({m["family"] for m in models} | set(saved or {}) | set((shown or {}).get("family_models", {})))
    rendered = forms.row_values(row, saved or {}, families)
    values = rendered if shown is None else shown
    return ctx.views.page(
        "channel_form", session=session, status=status, mode="edit", row=row,
        channel={"id": channel_id, "name": name}, values=values, orig=forms.snapshot(rendered, orig),
        models=models, families=families, aliases=list(values["alias_map"]), members=members,
        models_readable=saved is not None, error=error or (copy.CATALOG_DOWN if down else None),
        setup_unfinished=not row.active and row.inactive_reason in SETUP_REASONS)


async def new_form(ctx: AdminContext, request: Request, session, form) -> Response:
    channel_id = request.query_params.get("channel")
    if not channel_id:
        return redirect("/admin")

    async def build() -> Response:
        team = await team_id(ctx.channels)
        if await ctx.channels.is_registered(team, channel_id):  # a bookmarked or stale Register link
            return redirect(edit_url(team, channel_id))
        problem = await ctx.channels.check(channel_id)  # a guest or outside member: no form, just why
        if problem:
            return ctx.dead_end(request, problem, 422, session)
        return await register_page(ctx, session, channel_id)
    return await refusal_page(ctx, request, session, build())


async def edit_form(ctx: AdminContext, request: Request, session, form) -> Response:
    team, channel_id = request.path_params["team"], request.path_params["channel"]
    return await refusal_page(ctx, request, session, edit_page(ctx, request, session, team, channel_id))
