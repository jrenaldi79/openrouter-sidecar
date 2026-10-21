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
    return ctx.views.page("channels", session=session, overview=overview, no_channels_text=copy.NO_CHANNELS)


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


async def register_page(ctx: AdminContext, session, channel_id: str, *, shown: dict | None = None,
                        error: str | None = None, status: int = 200) -> Response:
    """The register form: blank, or showing a refused submission."""
    models, members = await ctx.channels.catalog(), await ctx.channels.members(channel_id)
    name = await slack_name(ctx.channels, channel_id)
    values = shown or forms.blank_values(next(iter(config.COHORT_WORKSPACES)), *ctx.register_defaults)
    families = sorted({m["family"] for m in models} | set(values["family_models"]))
    values = {**values, "family_models": {f: values["family_models"].get(f, "") for f in families}}
    return ctx.views.page(
        "channel_form", session=session, status=status, mode="register", row=None,
        channel={"id": channel_id, "name": name}, values=values, models=models, families=families,
        aliases=list(values["alias_map"]), members=members, error=error, setup_unfinished=False)


async def edit_page(ctx: AdminContext, request: Request, session, team: str, channel_id: str, *,
                    shown: dict | None = None, orig: dict | None = None, error: str | None = None,
                    status: int = 200) -> Response:
    """The edit form for a registered channel. With `shown`, the inputs keep a refused submission while the
    hidden originals stay the snapshot it was rendered from (`forms.FormValues`)."""
    found = await ctx.channels.channel(team, channel_id)
    if found is None:
        return ctx.dead_end(request, copy.NOT_REGISTERED, 404, session)
    row, saved = found
    models, members = await ctx.channels.catalog(), await ctx.channels.members(channel_id)
    name = await slack_name(ctx.channels, channel_id)
    families = sorted({m["family"] for m in models} | set(saved) | set((shown or {}).get("family_models", {})))
    rendered = forms.row_values(row, saved, families)
    values = rendered if shown is None else forms.FormValues(shown, {**rendered, **(orig or {})})
    return ctx.views.page(
        "channel_form", session=session, status=status, mode="edit", row=row,
        channel={"id": channel_id, "name": name}, values=values, models=models, families=families,
        aliases=list(rendered["alias_map"] if shown is None else shown["alias_map"]), members=members,
        error=error, setup_unfinished=not row.active and row.inactive_reason in SETUP_REASONS)


async def new_form(ctx: AdminContext, request: Request, session, form) -> Response:
    channel_id = request.query_params.get("channel")
    if not channel_id:
        return redirect("/admin")

    async def build() -> Response:
        problem = await ctx.channels.check(channel_id)  # a guest or outside member: no form, just why
        if problem:
            return ctx.dead_end(request, problem, 422, session)
        return await register_page(ctx, session, channel_id)
    return await refusal_page(ctx, request, session, build())


async def edit_form(ctx: AdminContext, request: Request, session, form) -> Response:
    team, channel_id = request.path_params["team"], request.path_params["channel"]
    return await refusal_page(ctx, request, session, edit_page(ctx, request, session, team, channel_id))
