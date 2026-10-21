"""Slack job runner: session files, the listed note, compare jobs and sandbox bundles."""

import pytest
from slack_runner_rig import NEWEST, Rig, answer_item, body, call

from openrouter_image_mcp.hosted.slack.context import CONTEXT_PATH, Context
from openrouter_image_mcp.hosted.slack.payload import FUSION_CHOICE, fusion_tool
from openrouter_image_mcp.hosted.slack.runner_steps import ChatFullError
from openrouter_image_mcp.session_files import FilePacket


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


def _context(*files, listed=()):
    return Context("ctx", NEWEST, list(files), list(listed))


def _file(name, chars):
    return FilePacket(path="slack/files/" + name, content="f" * chars)


def _paths(rig, chat):
    return {row["path"]: len(row["content"]) for row in rig.services.files.current(rig.scope, chat.id)}


def _last_user(request):
    return [item for item in request["input"] if item.get("role") == "user"][-1]["content"]


async def test_files_trimmed_to_session_room(slack_rig):
    """Ruling 2: overflow files are listed by name instead of failing the upsert."""
    rig = slack_rig
    chat = rig.chat()
    rig.services.files.upsert(rig.scope, chat.id, [_file("old-1.txt", 75_000), _file("old-2.txt", 75_000)])
    big, small = _file("new.csv", 60_000), _file("small.txt", 100)
    await rig.run(rig.job(chat, context=_context(big, small, listed=["deck.pdf"])))
    assert _paths(rig, chat) == {CONTEXT_PATH: 3, "slack/files/old-1.txt": 75_000,
                                 "slack/files/old-2.txt": 75_000, "slack/files/small.txt": 100}
    note = _last_user(rig.model.requests[0]).split("Files in the thread, not attached:\n", 1)[1]
    assert note.splitlines() == ["deck.pdf", "new.csv"]
    assert rig.job_rows()[0]["status"] == "completed"


async def test_replaced_file_does_not_count_against_room(slack_rig):
    rig = slack_rig
    chat = rig.chat()
    rig.services.files.upsert(rig.scope, chat.id, [_file("data.csv", 95_000), _file("other.txt", 60_000)])
    # Counting the old data.csv would leave 44,997 characters; replacing it leaves 139,997.
    await rig.run(rig.job(chat, context=_context(_file("data.csv", 90_000))))
    assert _paths(rig, chat) == {CONTEXT_PATH: 3, "slack/files/data.csv": 90_000, "slack/files/other.txt": 60_000}
    assert "not attached" not in _last_user(rig.model.requests[0])


async def test_history_keeps_plain_brief(slack_rig):
    """Ruling 3: the listed note goes to the model, not into the stored turn."""
    rig = slack_rig
    chat = rig.chat()
    await rig.run(rig.job(chat, brief="Summarize it", context=_context(listed=["deck.pdf"])))
    sent = _last_user(rig.model.requests[0])
    assert sent.startswith("Summarize it") and "deck.pdf" in sent and CONTEXT_PATH in sent
    assert rig.history(chat)[0] == {"role": "user", "content": "Summarize it"}


async def test_compare_has_no_sandbox_uploads_or_functions(slack_rig):
    """Ruling 4: compare is Fusion only, even in a sandbox chat."""
    rig = slack_rig
    chat = rig.chat(sandbox=True)
    job = rig.job(chat, compare=True, label="Fusion",
                  context=_context(FilePacket(path="slack/files/a.txt", content="x")))
    await rig.run(job)
    (request,) = rig.model.requests
    assert request["tools"] == [fusion_tool(rig.row, [])] and request["tool_choice"] == FUSION_CHOICE
    assert rig.model.uploads == []
    assert rig.texts()[-1].startswith("The answer.")


async def test_compare_every_panelist_failed(slack_rig):
    rig = slack_rig
    fusion = {"type": "openrouter:fusion", "status": "completed",
              "responses": [{"model": "a/one", "error": "timeout"}, {"model": "b/two", "status": "failed"}]}
    rig.model.respond = lambda request: body(fusion, answer_item("Analyst made this up."))
    await rig.run(rig.job(rig.chat(), compare=True, label="Fusion"))
    assert rig.texts()[-1].startswith("Every model in the panel failed: a/one, b/two.")


async def test_sandbox_uploads_bundle_and_cleans_up(slack_rig):
    rig = slack_rig
    chat = rig.chat(sandbox=True, mode="sandbox")
    job = rig.job(chat, mode="sandbox", context=_context(FilePacket(path="slack/files/a.csv", content="1,2")))
    await rig.run(job)
    (request,) = rig.model.requests
    shell = [tool for tool in request["tools"] if tool.get("type") == "openrouter:shell"]
    assert shell[0]["parameters"]["environment"]["file_ids"] == rig.model.uploads
    assert len(rig.model.uploads) == 1 and len(rig.model.deletes) == 1
    assert rig.job_rows()[0]["cleanup_pending"] == 0


async def test_sandbox_rejects_foreign_container(slack_rig):
    rig = slack_rig
    shell = {"type": "openrouter:shell", "id": "sh_1", "status": "completed", "container_id": "someone-else"}
    # The foreign container shows up in an earlier round, not in the final response.
    rig.model.respond = lambda request: body(shell, call()) if len(rig.model.requests) == 1 else body()
    chat = rig.chat(sandbox=True, mode="sandbox")
    await rig.run(rig.job(chat, mode="sandbox"))
    assert rig.job_rows()[0]["status"] == "failed"
    assert rig.history(chat) == []


async def test_full_session_asks_for_restart(slack_rig):
    """Stored files leave no room even for the context file: restart copy, not "try another model"."""
    rig = slack_rig
    chat = rig.chat()
    rig.services.files.upsert(rig.scope, chat.id, [_file("a.txt", 100_000), _file("b.txt", 99_999)])
    await rig.run(rig.job(chat, context=_context(_file("c.txt", 10))))
    assert rig.slack.updates[-1]["text"] == ChatFullError("Gemini").message
    assert rig.model.requests == [] and rig.job_rows()[0]["status"] == "failed"
