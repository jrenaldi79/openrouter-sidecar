"""The POSTs that change a channel: register, save, finish setup, turn off and turn on.

Each parses the form (`forms.py`), calls one `ChannelAdmin` operation and answers 303 to the edit page. A
`ChannelRefused` shows the form again with its sentence (422); htmx requests (Turn off and Turn on) get the plain
text its error banner shows. Anything else is a server error the guard turns into a generic page.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from ..slack.channel_setup import SETUP_REASONS, ChannelRefused
from . import forms
from .routes_base import AdminContext, is_htmx, redirect
from .routes_channels import edit_page, edit_url, refusal_page, register_page, team_id


def _path(request: Request) -> tuple[str, str]:
    return request.path_params["team"], request.path_params["channel"]


async def register(ctx: AdminContext, request: Request, session, form) -> Response:
    channel_id = request.query_params.get("channel")
    if not channel_id:
        return redirect("/admin")
    shown, team = forms.submitted_values(form), None
    try:
        team = await team_id(ctx.channels)
        await ctx.channels.register(team, channel_id, forms.new_row_fields(shown),
                                    forms.selected_models(shown), session)
    except ChannelRefused as refusal:
        return await _register_refused(ctx, request, session, team, channel_id, shown, refusal)
    return redirect(edit_url(team, channel_id))


async def _register_refused(ctx: AdminContext, request: Request, session, team: str | None, channel_id: str,
                            shown: dict, refusal: ChannelRefused) -> Response:
    """Before the row is written: the register form again with what was typed. After (a setup that stopped, or a
    double submit): the edit page, where Finish setup retries; it keeps the typed models for that retry."""
    found = await ctx.channels.channel(team, channel_id) if team is not None else None
    if found is None:
        build = register_page(ctx, session, channel_id, shown=shown, error=str(refusal), status=422)
    else:
        row = found[0]
        unfinished = not row.active and row.inactive_reason in SETUP_REASONS
        build = edit_page(ctx, request, session, team, channel_id, shown=shown if unfinished else None,
                          error=str(refusal), status=422)
    return await refusal_page(ctx, request, session, build)


async def refused_edit(ctx: AdminContext, request: Request, session, refusal: ChannelRefused, form) -> Response:
    """The edit form again with the sentence, keeping what was typed when the post carried the form."""
    if is_htmx(request):
        return PlainTextResponse(str(refusal), status_code=422)
    team, channel_id = _path(request)
    orig = forms.orig_values(form)
    shown = forms.submitted_values(form) if orig is not None else None
    return await refusal_page(ctx, request, session, edit_page(
        ctx, request, session, team, channel_id, shown=shown, orig=orig, error=str(refusal), status=422))


async def save(ctx: AdminContext, request: Request, session, form) -> Response:
    team, channel_id = _path(request)
    try:
        changes, family_changes = forms.changed(forms.submitted_values(form), forms.orig_values(form))
        await ctx.channels.change(team, channel_id, changes, family_changes, session)
    except ChannelRefused as refusal:
        return await refused_edit(ctx, request, session, refusal, form)
    return redirect(edit_url(team, channel_id))


async def finish(ctx: AdminContext, request: Request, session, form) -> Response:
    """Retry key and models for a channel whose setup stopped; the form's model selects are what to save."""
    team, channel_id = _path(request)
    try:
        await ctx.channels.finish_setup(team, channel_id, forms.selected_models(forms.submitted_values(form)),
                                        session)
    except ChannelRefused as refusal:
        return await refused_edit(ctx, request, session, refusal, form)
    return redirect(edit_url(team, channel_id))


async def turn_off(ctx: AdminContext, request: Request, session, form) -> Response:
    team, channel_id = _path(request)
    reason = form.get("reason")
    try:
        await ctx.channels.turn_off(team, channel_id, reason if isinstance(reason, str) else "", session)
    except ChannelRefused as refusal:
        return await refused_edit(ctx, request, session, refusal, form)
    return redirect(edit_url(team, channel_id))


async def turn_on(ctx: AdminContext, request: Request, session, form) -> Response:
    team, channel_id = _path(request)
    try:
        await ctx.channels.turn_on(team, channel_id, session)
    except ChannelRefused as refusal:
        return await refused_edit(ctx, request, session, refusal, form)
    return redirect(edit_url(team, channel_id))
