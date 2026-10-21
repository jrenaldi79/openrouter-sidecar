"""Shared fakes and builders for the admin view tests (rendering needs no database or Slack)."""
import re
from dataclasses import dataclass

from starlette.responses import HTMLResponse

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.views import Views
from openrouter_image_mcp.hosted.slack.registry import DEFAULT_ALIASES, new_row

XSS = "<script>alert(1)</script>"


@dataclass
class Sess:
    email: str = "ada@northwestern.edu"
    csrf_token: str = "c" * 32


@dataclass
class Entry:
    at: float
    actor_email: str
    action: str
    team_id: str
    channel_id: str
    label: str
    changes: dict


def body(response: HTMLResponse) -> str:
    return response.body.decode()


def row(**optional):
    base = {"fusion_panel": ("openai/gpt-x", "google/gemini-x"), "fusion_analyst": "openai/gpt-x",
                "subagent_model": "google/gemini-flash"}
    base.update(optional.pop("base", {}))
    return new_row("T1", "C1", optional.pop("label", "abbott"), "2027", **base, **optional)


MODELS = [
    {"id": "openai/gpt-x", "name": "GPT X", "family": "gpt"},
    {"id": "openai/gpt-y", "name": "GPT Y", "family": "gpt"},
    {"id": "google/gemini-x", "name": "Gemini X", "family": "gemini"},
    {"id": "google/gemini-flash", "name": "Gemini Flash", "family": "gemini"},
]


def values(r=None, **over):
    r = r or row()
    data = {
        "label": r.team_label, "cohort": r.cohort, "default_model": r.default_model,
        "alias_map": dict(r.alias_map), "fusion_panel": list(r.fusion_panel),
        "fusion_analyst": r.fusion_analyst, "subagent_model": r.subagent_model,
        "fetch_allowlist": list(r.fetch_allowlist), "daily_cap_usd": str(r.daily_cap_usd),
        "max_concurrent": str(r.max_concurrent), "invoker_allowlist": list(r.invoker_allowlist),
        "family_models": {"gpt": "openai/gpt-x", "gemini": "google/gemini-x"},
    }
    data.update(over)
    return data


def form_ctx(mode="edit", r=None, **over):
    r = r or row()
    ctx = {"mode": mode, "row": r if mode == "edit" else None, "channel": {"id": "C1", "name": "abbott-team"},
               "values": values(r), "models": MODELS, "families": ["gpt", "gemini"], "aliases": list(DEFAULT_ALIASES),
               "members": [("U1", "Grace Hopper"), ("U2", "Alan Turing")], "error": None, "setup_unfinished": False,
               "models_readable": True}
    ctx.update(over)
    ctx.setdefault("orig", ctx["values"])  # the snapshot the hidden `orig_*` fields carry
    return ctx


def overview(registered=(), unregistered=(), slack_note=None):
    return {"registered": list(registered), "unregistered": list(unregistered), "slack_note": slack_note}


def channels_html(**over):
    ctx = {"overview": overview(), "no_channels_text": copy.NO_CHANNELS}
    ctx.update(over)
    return body(Views().page("channels", session=Sess(), **ctx))


def nav_current(html: str) -> list[str]:
    return re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', html)


def posts(html: str) -> list[str]:
    return re.findall(r"<form\b[^>]*method=\"post\"[^>]*>.*?</form>", html, flags=re.DOTALL)
