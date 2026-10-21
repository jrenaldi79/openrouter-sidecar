"""`filter_catalog`: the model-list filter shared by the student settings and the admin page."""

from datetime import UTC, datetime
from types import SimpleNamespace

from openrouter_image_mcp.preferences import (
    MAX_MODELS,
    PreferenceService,
    filter_catalog,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
RECENT = int(NOW.timestamp())
KEY = "sk-or-v1-synthetic-catalog"
SCOPE = SimpleNamespace(owner="owner-fingerprint", workspace="ws-1")


def _row(model_id, **extra):
    base = {"id": model_id, "name": model_id.split("/")[-1], "pricing": {"prompt": "0.001"},
            "context_length": 1000, "created": RECENT}
    return base | extra


ROWS = [
    _row("google/gemini-a"),
    _row("google/gemini-a"),  # duplicate id
    _row("openai/gpt-a", name="GPT (Batch)"),  # batch by name
    _row("openai/gpt-b:batch"),  # batch by id
    _row("qwen/qwen-a", pricing={"prompt": "-1", "completion": "0.5", "image": "nan"}),
    _row("x-ai/grok-a", context_length=2**40),
    _row("x-ai/grok-old", created=1),
    _row("mistralai/other"),  # no approved family
    _row("openai/" + KEY),  # secret in the id
    _row("google/gemini-named", name="called " + KEY + " ok"),
    _row("bad id/with space"),
    "not a row",
]


class _Advisors:
    store = SimpleNamespace(target="unused")

    def __init__(self, rows):
        self.rows = rows

    async def models(self):
        return self.rows

    def _current(self, scope):
        pass


async def test_filter_catalog_matches_student_catalog():
    student = await PreferenceService(_Advisors(ROWS), utcnow=lambda: NOW)._catalog(KEY, SCOPE)
    shared = filter_catalog(ROWS, NOW, secrets=(KEY, SCOPE.owner))
    assert shared == student
    assert [m["id"] for m in shared] == ["google/gemini-a", "qwen/qwen-a", "x-ai/grok-a", "google/gemini-named"]
    by_id = {m["id"]: m for m in shared}
    assert by_id["qwen/qwen-a"]["pricing"] == {"completion": "0.5"}
    assert by_id["x-ai/grok-a"]["context_length"] is None
    assert KEY not in by_id["google/gemini-named"]["name"]
    assert set(shared[0]) == {"id", "name", "pricing", "context_length", "created", "family"}


def test_filter_catalog_empty_secret_keeps_rows():
    rows = [_row("google/gemini-a"), _row("openai/gpt-a")]
    assert [m["id"] for m in filter_catalog(rows, NOW)] == ["google/gemini-a", "openai/gpt-a"]
    assert filter_catalog(rows, NOW, secrets=("", "")) == filter_catalog(rows, NOW)


def test_filter_catalog_caps_the_list():
    rows = [_row(f"google/gemini-{i}") for i in range(MAX_MODELS + 5)]
    assert len(filter_catalog(rows, NOW)) == MAX_MODELS
