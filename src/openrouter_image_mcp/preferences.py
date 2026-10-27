"""Local, credential/workspace-scoped sidecar choices; no inference calls."""

from __future__ import annotations

import json
import re
import secrets
from calendar import monthrange
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from . import storage
from .advisor_images import vision
from .advisor_store import AdvisorAccessError
from .config import COHORT_WORKSPACES
from .errors import BadRequestError
from .logs import redact

FAMILIES = ("google", "openai", "x-ai", "open_weight")
ALIASES = {"gemini": "google", "google": "google", "chatgpt": "openai",
           "chat gpt": "openai", "gpt": "openai", "openai": "openai",
           "open weight": "open_weight", "openweight": "open_weight",
           "open-weight": "open_weight", "open source": "open_weight",
           "grok": "x-ai", "xai": "x-ai", "x-ai": "x-ai"}
MAX_MODELS = 1000
_MODEL_ID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/-]+$")
_PRICE_FIELDS = ("prompt", "completion", "request", "image", "web_search",
                 "internal_reasoning", "input_cache_read", "input_cache_write")
_SETTINGS_ERROR = "Sidecar settings changed or are unavailable for this sign-in. Refresh settings."
_MODEL_ERROR = "Choose an available exact advisor model ID; reopen sidecar settings."
# Matched by the Slack bot (hosted/slack/commands.py) to tell a team its alias has no model yet.
NO_DEFAULT = "No advisor default selected; reopen sidecar settings."


def model_family(model_id):
    """Classify only the approved vendors and their canonical model families."""
    if not isinstance(model_id, str) or "/" not in model_id:
        return None
    vendor, model = model_id.casefold().split("/", 1)
    if vendor in ("google", "openai"):
        return vendor
    if vendor == "x-ai" and re.match(r"grok(?:[-\d.:/]|$)", model):
        return vendor
    prefixes = {"z-ai": "glm", "thudm": "glm", "qwen": "qwen",
                "moonshotai": "kimi", "minimax": "minimax", "deepseek": "deepseek"}
    prefix = prefixes.get(vendor)
    if prefix and model.startswith(prefix):
        return "open_weight"
    return None


def six_month_cutoff(now):
    """Six calendar months earlier in UTC, clamping the day to that month."""
    now = now.astimezone(UTC)
    month_index = now.year * 12 + now.month - 1 - 6
    year, month = divmod(month_index, 12)
    month += 1
    return now.replace(year=year, month=month,
                       day=min(now.day, monthrange(year, month)[1]))


def _valid_id(value, secrets):
    return (isinstance(value, str) and 1 <= len(value) <= 200
            and _MODEL_ID.fullmatch(value) is not None
            and not any(secret in value for secret in secrets) and redact(value) == value)


def _text(value, secrets, default):
    if not isinstance(value, str):
        return default
    # Credential fingerprints are private too, even in hostile provider fields.
    for secret in secrets:
        value = value.replace(secret, "[redacted]")
    return redact(value)[:200]


def _pricing(value, secrets):
    if not isinstance(value, dict):
        return {}
    result = {}
    for field in _PRICE_FIELDS:
        price = value.get(field)
        if (type(price) not in (str, int, float) or len(str(price)) > 64
                or any(secret in str(price) for secret in secrets)):
            continue
        try:
            number = Decimal(str(price))
        except InvalidOperation:
            continue
        if number.is_finite() and number >= 0:
            result[field] = str(price)
    return result


def filter_catalog(rows, now, *, secrets=()):
    """The advisor models a person may pick: approved families, no batch variants, created in the last six
    months, deduplicated and capped. A row whose id holds a secret is dropped and a secret in a name or price
    is redacted; empty secrets are ignored (they would match everything). Students pass their key and owner."""
    secrets = tuple(secret for secret in secrets if secret)
    now = now.astimezone(UTC)
    oldest, newest = six_month_cutoff(now).timestamp(), now.timestamp()
    result, ids = [], set()
    for row in rows:
        if not isinstance(row, dict) or not _valid_id(row.get("id"), secrets):
            continue
        model_id = row["id"]
        if any(re.search(r"(?:^|[^a-z0-9])batch(?:$|[^a-z0-9])", value, re.IGNORECASE)
               for value in (model_id, row.get("name", "")) if isinstance(value, str)):
            continue
        family = model_family(model_id)
        created = row.get("created")
        if family is None or type(created) is not int or not oldest <= created <= newest:
            continue
        if model_id in ids:
            continue
        ids.add(model_id)
        context = row.get("context_length")
        if type(context) is not int or not 0 < context <= 2**31 - 1:
            context = None
        result.append({"id": model_id, "name": _text(row.get("name"), secrets, model_id),
                       "pricing": _pricing(row.get("pricing"), secrets), "context_length": context,
                       "created": created, "family": family,
                       "vision": row.get("vision") is True or vision(row)})
        if len(result) >= MAX_MODELS:
            break
    return result


def ensure_table(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS sidecar_preferences (
        settings_id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0, general_default TEXT,
        family_defaults TEXT NOT NULL, onboarding_completed INTEGER NOT NULL DEFAULT 0,
        UNIQUE(owner, workspace)
    )""")


class PreferenceService:
    def __init__(self, advisors, *, utcnow=None):
        self.advisors = advisors
        self.target = advisors.store.target
        self._utcnow = utcnow or (lambda: datetime.now(UTC))

    @contextmanager
    def _connection(self):
        with storage.connect(self.target) as db:
            ensure_table(db)
            db.commit()
            yield db

    @staticmethod
    def _row(db, scope):
        return db.execute("SELECT * FROM sidecar_preferences WHERE owner=? AND workspace=?",
                          (scope.owner, scope.workspace)).fetchone()

    @staticmethod
    def _public(row):
        families = json.loads(row["family_defaults"])
        return {"settings_id": row["settings_id"], "revision": row["revision"],
                "general_default": row["general_default"],
                "family_defaults": {family: families.get(family) for family in FAMILIES},
                "onboarding_completed": bool(row["onboarding_completed"])}

    def _get(self, scope):
        self.advisors._current(scope)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope)
            if row is None:
                self.advisors._current(scope)
                db.execute("INSERT INTO sidecar_preferences "
                           "(settings_id, owner, workspace, family_defaults) VALUES (?,?,?,?)",
                           ("settings_" + secrets.token_hex(16), scope.owner, scope.workspace,
                            json.dumps(dict.fromkeys(FAMILIES))))
                row = self._row(db, scope)
            return self._public(row)

    def get(self):
        _, scope = self.advisors._credentials()
        return self._get(scope)

    @staticmethod
    def _check(row, settings_id, revision=None):
        if (not isinstance(settings_id, str) or len(settings_id) > 100 or row is None
                or settings_id != row["settings_id"]
                or revision is not None and revision != row["revision"]):
            raise AdvisorAccessError(_SETTINGS_ERROR)

    def assert_context(self, settings_id):
        _, scope = self.advisors._credentials()
        self.advisors._current(scope)
        with self._connection() as db:
            self._check(self._row(db, scope), settings_id)

    async def _catalog(self, key, scope):
        try:
            rows = await self.advisors.models()
        finally:
            self.advisors._current(scope)
        if not isinstance(rows, list):
            raise BadRequestError("Advisor catalog is unavailable; refresh sidecar settings.")
        return filter_catalog(rows, self._utcnow(), secrets=(key, scope.owner))

    async def view(self):
        key, scope = self.advisors._credentials()
        models = await self._catalog(key, scope)
        self.advisors._current(scope)
        cohort = next((year for year, workspace in COHORT_WORKSPACES.items()
                       if workspace == scope.workspace), None)
        return self._get(scope) | {"models": models, "workspace_id": scope.workspace, "cohort": cohort}

    @staticmethod
    def _model_field(value):
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            raise BadRequestError(_MODEL_ERROR)

    async def update(self, settings_id, expected_revision, general_default=None,
                     family_defaults=None, onboarding_completed=None):
        key, scope = self.advisors._credentials()
        if type(expected_revision) is not int or expected_revision < 0:
            raise BadRequestError("Supply a valid settings revision; refresh sidecar settings.")
        self.advisors._current(scope)
        with self._connection() as db:
            self._check(self._row(db, scope), settings_id, expected_revision)
        self._model_field(general_default)
        if family_defaults is not None:
            if not isinstance(family_defaults, dict) or any(family not in FAMILIES for family in family_defaults):
                raise BadRequestError("Use only google, openai or open_weight family defaults.")
            family_defaults = dict(family_defaults)
            for value in family_defaults.values():
                self._model_field(value)
        if onboarding_completed is not None and type(onboarding_completed) is not bool:
            raise BadRequestError("Onboarding completion must be true or false.")
        choices = [(None, general_default)] + list((family_defaults or {}).items())
        if any(value for _, value in choices):
            catalog = {row["id"]: row for row in await self._catalog(key, scope)}
            self.advisors._current(scope)
            for family, value in choices:
                if value and (value not in catalog or family is not None and catalog[value]["family"] != family):
                    raise BadRequestError(_MODEL_ERROR)
        self.advisors._current(scope)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope)
            self._check(row, settings_id, expected_revision)
            saved = self._public(row)
            if general_default is not None:
                saved["general_default"] = general_default or None
            for family, value in (family_defaults or {}).items():
                if value is not None:
                    saved["family_defaults"][family] = value or None
            if onboarding_completed is not None:
                saved["onboarding_completed"] = onboarding_completed
            self.advisors._current(scope)
            db.execute("UPDATE sidecar_preferences SET revision=revision+1,general_default=?,"
                       "family_defaults=?,onboarding_completed=? WHERE settings_id=? AND owner=? AND workspace=?",
                       (saved["general_default"], json.dumps(saved["family_defaults"]),
                        int(saved["onboarding_completed"]), settings_id, scope.owner, scope.workspace))
            return self._public(self._row(db, scope))

    async def resolve(self, model=None):
        key, scope = self.advisors._credentials()
        self._model_field(model)
        saved = self._get(scope)
        family = ALIASES.get(model.strip().casefold()) if isinstance(model, str) else None
        if model is None:
            selected, source = saved["general_default"], "general_default"
        elif family is not None:
            selected, source = saved["family_defaults"][family], "family_default"
        else:
            selected, source = model, "explicit"
        if not selected:
            raise BadRequestError(NO_DEFAULT)
        models = {row["id"]: row for row in await self._catalog(key, scope)}
        self.advisors._current(scope)
        if selected not in models or family is not None and models[selected]["family"] != family:
            raise BadRequestError(_MODEL_ERROR)
        return {"model": selected, "name": models[selected]["name"], "source": source}
