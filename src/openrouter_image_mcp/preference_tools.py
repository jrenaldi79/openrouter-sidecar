"""Conversation and MCP App entry points for defaults and deliberate demos."""

import inspect
import json
from decimal import Decimal, InvalidOperation
from typing import Literal

from mcp.types import CallToolResult, TextContent

from .apps import SETTINGS_UI_URI
from .catalog import usable_demo_image
from .errors import AuthRequiredError, OpenRouterError
from .logs import redact

PREFERENCE_ARGUMENTS = {
    "open_sidecar_settings": {"mode"},
    "get_sidecar_preferences": set(),
    "set_sidecar_preferences": {"settings_id", "expected_revision", "general_default",
                                "family_defaults", "onboarding_completed"},
    "resolve_advisor_model": {"model"},
    "get_sidecar_settings": set(),
    "run_sidecar_demo": {"demo", "settings_id", "expected_revision", "image_model"},
    "get_sidecar_demo": {"demo_id", "settings_id"},
}


def image_choices(images, key, scope):
    choices = []
    for item in images:
        if (not usable_demo_image(item) or not isinstance(item.id, str) or len(item.id) > 200 or key in item.id
                or scope.owner in item.id):
            continue
        pricing = {}
        for field in ("image_output", "image", "request", "prompt", "completion"):
            value = item.pricing.get(field)
            if (type(value) not in (str, int, float) or len(str(value)) > 64
                    or key in str(value) or scope.owner in str(value)):
                continue
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                continue
            if number.is_finite() and number >= 0:
                pricing[field] = str(value)
        name = redact(item.name.replace(key, "[withheld]").replace(scope.owner, "[withheld]"))[:200]
        lines = []
        for line in item.pricing_lines[:50]:
            if not isinstance(line, dict) or line.get("unit") not in {"image", "megapixel", "token", "request"}:
                continue
            value = line.get("cost_usd")
            if (type(value) not in (str, int, float) or len(str(value)) > 64
                    or key in str(value) or scope.owner in str(value)):
                continue
            try:
                amount = Decimal(str(value))
            except InvalidOperation:
                continue
            if not amount.is_finite() or amount < 0:
                continue
            safe = {"unit": line["unit"], "cost_usd": str(value)}
            for field in ("billable", "variant"):
                text = line.get(field)
                if isinstance(text, str):
                    safe[field] = redact(text.replace(key, "[withheld]").replace(scope.owner, "[withheld]"))[:80]
            lines.append(safe)
        choices.append({"id": item.id, "name": name, "pricing": pricing,
                        "pricing_lines": lines, "context_length": None})
        if len(choices) == 100:
            break
    return choices


def register_preference_tools(server, preferences, demos, catalog, settings, tool_errors):
    def result(value, *, meta=None):
        return CallToolResult(content=[TextContent(type="text", text=redact(json.dumps(value)))],
                              structured_content=value, meta=meta)

    def target():
        return {"workspace_id": settings.workspace_id, "cohort": settings.cohort}

    async def open_sidecar_settings(mode: Literal["onboarding", "settings"] = "onboarding") -> CallToolResult:
        """Open OpenRouter Sidecar setup or change defaults at any time.

        The app confirms sign-in/cohort, saves default Gemini/ChatGPT/Grok/Open Weight and
        general advisor models, and visually explains advisors, skill handoffs,
        images, and selective sharing. Opening the guide and saving settings do
        not run inference. Hosts without Apps can use the preferences tools.
        """
        with tool_errors():
            try:
                preferences.get()
                signed_in = True
            except AuthRequiredError:
                signed_in = False
            return result({"mode": mode, "signed_in": signed_in, **target()})

    async def get_sidecar_preferences() -> CallToolResult:
        """Read saved advisor defaults, opaque settings ID and save revision; no inference.

        Preferences belong to the signed-in account and its class cohort.
        Use these settings_id and revision values when changing defaults.
        """
        with tool_errors():
            return result(preferences.get())

    async def set_sidecar_preferences(
        settings_id: str, expected_revision: int, general_default: str | None = None,
        family_defaults: dict[str, str] | None = None, onboarding_completed: bool | None = None,
    ) -> CallToolResult:
        """Save advisor defaults or mark the walkthrough complete; no paid inference.

        First get_sidecar_preferences for settings_id and revision. Pass exact IDs
        from list_chat_models; family keys are google, openai, x-ai and open_weight.
        Omitted fields stay unchanged. An empty string clears a selected default.
        Changes affect new chats; existing chats keep their selected model.
        Stale revisions or another sign-in's settings ID are rejected.
        """
        with tool_errors():
            return result(await preferences.update(settings_id, expected_revision,
                general_default=general_default, family_defaults=family_defaults,
                onboarding_completed=onboarding_completed))

    async def resolve_advisor_model(model: str | None = None) -> CallToolResult:
        """Resolve Gemini/Google, ChatGPT/GPT/OpenAI, Open Weight or a one-off exact ID.

        Omit model for the saved general default. Returns the exact model and name.
        Never changes preferences or runs inference. Missing/retired choices ask
        the user to change settings instead of silently substituting another model.
        """
        with tool_errors():
            return result(await preferences.resolve(model))

    async def get_sidecar_settings() -> CallToolResult:
        """App-only: load settings and authenticated choices without inference."""
        with tool_errors():
            try:
                view = await preferences.view()
            except AuthRequiredError:
                return result({"signed_in": False, **target()},
                              meta={"sidecarSettings": {"signed_in": False, **target()}})
            key, scope = preferences.advisors._credentials()
            preferences.assert_context(view["settings_id"])
            try:
                images = await catalog.all()
                view["image_models"] = image_choices(images, key, scope)
            except OpenRouterError:
                view["image_models"] = []
                view["image_error"] = "Image demo choices are unavailable; other demos remain usable."
            preferences.advisors._current(scope)
            preferences.assert_context(view["settings_id"])
            view["signed_in"] = True
            return result({"signed_in": True, "revision": view["revision"], **target()},
                          meta={"sidecarSettings": view})

    async def run_sidecar_demo(
        demo: Literal["hello", "history", "skill", "image"], settings_id: str,
        expected_revision: int, image_model: str | None = None,
    ) -> CallToolResult:
        """App-only: start one deliberately selected tiny paid demo, without automatic retry.

        Fixed synthetic prompts only. Text output is limited to 256 tokens and
        one sandbox tool step. These limits are not a hard dollar cap. A repeated
        request for this settings revision returns the same attempt. Image demo
        uses one image and the explicitly selected exact image model.
        """
        with tool_errors():
            if demo == "image":
                return result(await demos.run_image(settings_id, expected_revision, image_model))
            return result(await demos.run(demo, settings_id, expected_revision))

    async def get_sidecar_demo(demo_id: str, settings_id: str) -> CallToolResult:
        """App-only: display an owned image demonstration; reading never runs inference."""
        with tool_errors():
            view = demos.view_image(demo_id, settings_id)
            return result({key: value for key, value in view.items() if key != "images"},
                          meta={"sidecarDemo": view})

    private = {"get_sidecar_settings", "run_sidecar_demo", "get_sidecar_demo"}
    for fn in (open_sidecar_settings, get_sidecar_preferences, set_sidecar_preferences,
               resolve_advisor_model, get_sidecar_settings, run_sidecar_demo, get_sidecar_demo):
        meta = None
        if fn.__name__ == "open_sidecar_settings" or fn.__name__ in private:
            ui = {"resourceUri": SETTINGS_UI_URI}
            if fn.__name__ in private:
                ui["visibility"] = ["app"]
            meta = {"ui": ui}
        server.add_tool(fn, description=inspect.cleandoc(fn.__doc__), structured_output=False, meta=meta)
