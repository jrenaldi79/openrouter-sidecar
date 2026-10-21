"""The channel form as data: what a submission says, and which fields this admin changed.

Field names are the contract in `templates/CLAUDE.md`. The edit page carries a hidden JSON copy of every value as
rendered (`orig_<field>`), so a save sends only what differs from it and two admins editing different fields of
one channel don't overwrite each other. Parsing never refuses: text that is not a number passes through as text
and the registry's own validation says so in a sentence.
"""

from __future__ import annotations

import json
import re

from ..slack.channel_admin import EDITABLE
from ..slack.registry import DEFAULT_ALIASES

DEFAULT_ALIAS = "gemini"
# The `orig_*` fields the edit form renders; `family_models` is the saved per-family model of the team.
ORIG_FIELDS = (*sorted(EDITABLE), "family_models")
_MODEL_FIELD = re.compile(r"^model_([\w-]+)$", re.ASCII)
_ORDER_FREE = {"invoker_allowlist"}  # checkboxes come back in name order, not the stored order


def _strings(form, name: str) -> list[str]:
    return [value for value in form.getlist(name) if isinstance(value, str)]


def _text(form, name: str) -> str:
    return next(iter(_strings(form, name)), "").strip()


def _lines(items: list[str]) -> list[str]:
    """Stripped, non-blank, first occurrence only."""
    found: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in found:
            found.append(item)
    return found


def _aliases(form) -> dict[str, str]:
    pairs = zip(_strings(form, "alias_name"), _strings(form, "alias_target"), strict=False)
    return {name.strip(): target.strip() for name, target in pairs if name.strip()}


def _invokers(form) -> list[str]:
    # With no member names the page shows a textarea, which is always submitted (even empty).
    text = _strings(form, "invoker_allowlist_text")
    return _lines(text[0].splitlines() if text else _strings(form, "invoker_allowlist"))


def submitted_values(form) -> dict:
    """The form's fields in the shape the template shows them (`values` in `channel_form.html`)."""
    models = {}
    for name in form:
        found = _MODEL_FIELD.match(name)
        if found:
            models[found.group(1)] = _text(form, name)
    fetch = _strings(form, "fetch_allowlist")
    return {
        "label": _text(form, "label"), "cohort": _text(form, "cohort"),
        "default_model": _text(form, "default_model"), "alias_map": _aliases(form),
        "fusion_panel": _lines(_strings(form, "fusion_panel")),
        "fusion_analyst": _text(form, "fusion_analyst"), "subagent_model": _text(form, "subagent_model"),
        "fetch_allowlist": _lines(fetch[0].splitlines() if fetch else []),
        "daily_cap_usd": _text(form, "daily_cap_usd"), "max_concurrent": _text(form, "max_concurrent"),
        "invoker_allowlist": _invokers(form), "family_models": models,
    }


def blank_values(cohort: str, daily_cap_usd: float, max_concurrent: int) -> dict:
    """The register form before anything is typed."""
    return {
        "label": "", "cohort": cohort, "default_model": DEFAULT_ALIAS, "alias_map": dict(DEFAULT_ALIASES),
        "fusion_panel": [], "fusion_analyst": "", "subagent_model": "", "fetch_allowlist": [],
        "daily_cap_usd": str(daily_cap_usd), "max_concurrent": str(max_concurrent), "invoker_allowlist": [],
        "family_models": {},
    }


def row_values(row, saved: dict, families) -> dict:
    """The edit form as rendered for `row`; `saved` is the team's per-family models."""
    return {
        "label": row.team_label, "cohort": row.cohort, "default_model": row.default_model,
        "alias_map": dict(row.alias_map), "fusion_panel": list(row.fusion_panel),
        "fusion_analyst": row.fusion_analyst, "subagent_model": row.subagent_model,
        "fetch_allowlist": list(row.fetch_allowlist), "daily_cap_usd": str(row.daily_cap_usd),
        "max_concurrent": str(row.max_concurrent), "invoker_allowlist": list(row.invoker_allowlist),
        "family_models": {family: saved.get(family) or "" for family in families},
    }


def _number(value, kind):
    try:
        return kind(str(value).strip())
    except (TypeError, ValueError):
        return value  # the registry refuses it with its own sentence


def typed(values: dict) -> dict:
    """The editable fields as the registry takes them: numbers parsed, everything else as shown."""
    found = {name: values[name] for name in EDITABLE if name in values}
    if "daily_cap_usd" in found:
        found["daily_cap_usd"] = _number(found["daily_cap_usd"], float)
    if "max_concurrent" in found:
        found["max_concurrent"] = _number(found["max_concurrent"], int)
    return found


def new_row_fields(values: dict) -> dict:
    """What `ChannelAdmin.register` takes: the label and cohort with the typed fields (rows hold tuples)."""
    fields = {**typed(values), "label": values["label"], "cohort": values["cohort"]}
    for name in ("fetch_allowlist", "invoker_allowlist"):
        fields[name] = tuple(fields[name])
    return fields


def selected_models(values: dict) -> dict[str, str]:
    """The per-family models picked in the form, blanks left out."""
    return {family: model for family, model in values.get("family_models", {}).items() if model}


def orig_values(form) -> dict | None:
    """The hidden `orig_*` snapshot the edit form was rendered from; None when the post carries none."""
    found = {}
    for name in ORIG_FIELDS:
        raw = _text(form, f"orig_{name}")
        try:
            found[name] = json.loads(raw)
        except ValueError:
            continue
    return found or None


def _differs(name: str, now, before) -> bool:
    if name in _ORDER_FREE and isinstance(now, list) and isinstance(before, list):
        return sorted(map(str, now)) != sorted(map(str, before))
    return now != before


def changed(shown: dict, orig: dict | None) -> tuple[dict, dict[str, str]]:
    """`(registry changes, family model changes)`: what differs from the rendered snapshot.

    A field with no snapshot counts as changed, so a post without originals still saves what it says."""
    orig = orig or {}
    now, before = typed(shown), typed(orig)
    changes = {name: now[name] for name in EDITABLE
               if name in now and (name not in before or _differs(name, now[name], before[name]))}
    rendered = orig.get("family_models") if isinstance(orig.get("family_models"), dict) else {}
    models = {family: model for family, model in selected_models(shown).items()
              if model != (rendered.get(family) or "")}
    return changes, models


class FormValues:
    """`values` for a form shown again after a refusal.

    The inputs show what was submitted, but the hidden `orig_*` copies must stay the snapshot the form was first
    rendered from, or a retry would see the unsaved edits as unchanged and drop them. The template reads inputs
    as attributes (`values.label`) and the snapshot by key (`values[field]`), which this keeps apart."""

    def __init__(self, shown: dict, orig: dict | None):
        self._shown, self._orig = shown, {**shown, **(orig or {})}

    def __getattr__(self, name: str):
        try:
            return self._shown[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name: str):
        return self._orig[name]
