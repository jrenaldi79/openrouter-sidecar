"""Slack job runner: queue, job row, progress, answer, post and stop (rig in slack_runner_rig.py)."""

import asyncio

import pytest
from slack_runner_rig import (
    CHANNEL,
    KEY,
    NEWEST,
    THREAD,
    Rig,
    answer_item,
    body,
    call,
    wait_until,
)

from openrouter_image_mcp.hosted.slack import runner as runner_module
from openrouter_image_mcp.hosted.slack.context import CONTEXT_PATH, Context
from openrouter_image_mcp.hosted.slack.slack_tools import FUNCTION_NAMES


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


async def test_happy_path_posts_answer_with_footer(slack_rig):
    rig = slack_rig
    chat = rig.chat()
    await rig.run(rig.job(chat))
    working, answer = rig.slack.posts
    assert working["text"] == "Sidecar · Gemini is working…"
    assert answer["username"] == "Sidecar · Gemini" and answer["thread_ts"] == THREAD
    assert answer["text"] == "The answer.\n\nGemini · g/model · $0.0100"
    assert rig.slack.updates[-1] == {"channel": CHANNEL, "ts": working["ts"],
                                     "text": "Sidecar · Gemini answered below."}
    (row,) = rig.job_rows()
    assert (row["status"], row["answer"], row["cost_usd"]) == ("completed", "The answer.", 0.01)
    assert row["slack_posted_at"] is not None
    assert rig.history(chat)[-2:] == [{"role": "user", "content": "What should we do?"},
                                      {"role": "assistant", "content": "The answer."}]
    seen = rig.threads.last_answered(rig.scope, CHANNEL, THREAD)
    assert (seen.chat_id, seen.last_seen_ts) == (chat.id, NEWEST)
    (request,) = rig.model.requests
    assert request["store"] is False and request["model"] == "g/model"
    assert FUNCTION_NAMES <= {tool.get("name") for tool in request["tools"]}


async def test_context_file_replaced_not_added(slack_rig):
    rig = slack_rig
    chat = rig.chat()
    await rig.run(rig.job(chat, context=Context("first context", "1700000000.000002")))
    await rig.run(rig.job(chat, context=Context("second context", "1700000000.000003")))
    rows = rig.services.files.current(rig.scope, chat.id)
    assert [(r["path"], r["version"], r["content"]) for r in rows] == [(CONTEXT_PATH, 2, "second context")]


async def test_queue_waits_then_runs(slack_rig, monkeypatch):
    """Review Focus 3: a second ask on a busy chat waits for it, then runs."""
    rig = slack_rig
    monkeypatch.setattr(runner_module, "IDLE_POLL_S", 0.001)
    gate = asyncio.Event()

    async def respond(request):
        number = len(rig.model.requests)
        if number == 1:
            await gate.wait()
        return body(answer_item(f"answer {number}"))

    rig.model.respond = respond
    chat = rig.chat()
    first = asyncio.create_task(rig.run(rig.job(chat, brief="one")))
    await wait_until(lambda: len(rig.model.requests) == 1)
    second = asyncio.create_task(rig.run(rig.job(chat, brief="two")))
    await wait_until(lambda: rig.runner.running(rig.scope.owner, CHANNEL) == ["Gemini", "Gemini"])
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(rig.model.requests) == 1
    gate.set()
    await asyncio.gather(first, second)
    assert [r["status"] for r in rig.job_rows()] == ["completed", "completed"]
    answers = [text for text in rig.texts() if text.startswith("answer")]
    assert [a.split("\n")[0] for a in answers] == ["answer 1", "answer 2"]
    assert [m["content"] for m in rig.history(chat) if m["role"] == "user"] == ["one", "two"]


async def test_waits_for_another_process_job_on_the_chat(slack_rig, monkeypatch):
    """The chat lock is per process; another replica's active job is seen through the job rows."""
    rig = slack_rig
    monkeypatch.setattr(runner_module, "IDLE_POLL_S", 0.001)
    chat = rig.chat()
    other = rig.results.create(rig.scope, chat, "elsewhere", "proc-other")
    polls = []
    real = rig.results.active_for_chat
    rig.results.active_for_chat = lambda *args: polls.append(1) or real(*args)
    task = asyncio.create_task(rig.run(rig.job(chat)))
    await wait_until(lambda: len(polls) >= 3)
    assert rig.model.requests == []
    rig.results.update(rig.scope, other["id"], "proc-other", status="completed")
    await task
    assert rig.texts()[-1].startswith("The answer.")


async def test_queue_timeout_posts_limit(slack_rig, monkeypatch):
    rig = slack_rig
    rig.set_row(max_concurrent=1)
    monkeypatch.setattr(runner_module, "QUEUE_WAIT_S", 0.01)
    gate = asyncio.Event()

    async def respond(request):
        await gate.wait()
        return body()

    rig.model.respond = respond
    first = asyncio.create_task(rig.run(rig.job(rig.chat())))
    await wait_until(lambda: len(rig.model.requests) == 1)
    await rig.run(rig.job(rig.chat(alias="gpt"), alias="gpt", label="ChatGPT"))
    limit = rig.slack.posts[-1]
    assert limit["text"] == "Your team already has 1 answers running. Try again in a minute."
    assert limit["username"] == "Sidecar · ChatGPT" and limit["thread_ts"] == THREAD
    gate.set()
    await first
    assert len(rig.job_rows()) == 1


async def test_long_answer_uploaded_as_file(slack_rig):
    rig = slack_rig
    text = "\n\n".join(["word " * 600] * 5)
    rig.model.respond = lambda request: body(answer_item(text))
    await rig.run(rig.job(rig.chat()))
    (upload,) = rig.slack.uploads
    assert upload["filename"] == "answer.md" and upload["thread_ts"] == THREAD
    assert upload["content"] == text.strip().encode()
    assert rig.texts()[-1] == "Gemini · g/model · $0.0100"
    assert rig.job_rows()[0]["slack_posted_at"] is not None


async def test_progress_edit_failure_ignored(slack_rig):
    """Review Focus 5: a 429 on chat.update never fails the job."""
    rig = slack_rig

    def respond(request):
        if len(rig.model.requests) == 1:
            for _ in range(10):
                rig.slack.fail_next("update", "ratelimited", 1.0)
            return body({"type": "openrouter:web_search", "id": "ws_1", "status": "completed"}, call())
        return body()

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))
    assert rig.texts()[-1].startswith("The answer.")
    assert rig.job_rows()[0]["status"] == "completed"


async def test_progress_steps_name_the_work(slack_rig):
    rig = slack_rig

    def respond(request):
        if len(rig.model.requests) == 1:
            return body({"type": "openrouter:web_search", "id": "ws_1", "status": "completed"}, call())
        return body()

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))
    steps = [u["text"] for u in rig.slack.updates]
    assert steps == ["Searched the web", "Read the thread", "Sidecar · Gemini answered below."]


async def test_final_post_retried_then_left_unposted(slack_rig):
    """Review Focus 5: two failed posts leave slack_posted_at NULL for restart recovery."""
    rig = slack_rig

    def respond(request):
        rig.slack.fail_next("post", "internal_error")
        rig.slack.fail_next("post", "internal_error")
        return body()

    rig.model.respond = respond
    chat = rig.chat()
    await rig.run(rig.job(chat))
    (row,) = rig.job_rows()
    assert row["status"] == "completed" and row["slack_posted_at"] is None
    assert len(rig.slack.posts) == 1  # only the working message
    assert rig.threads.last_answered(rig.scope, CHANNEL, THREAD) is None


async def test_final_post_retried_once_then_posted(slack_rig):
    rig = slack_rig

    def respond(request):
        rig.slack.fail_next("post", "internal_error")
        return body()

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))
    assert rig.texts()[-1].startswith("The answer.")
    assert rig.job_rows()[0]["slack_posted_at"] is not None


async def test_stop_discards_result(slack_rig):
    rig = slack_rig
    stopped = []

    def respond(request):
        stopped.append(rig.runner.stop(rig.scope.owner, CHANNEL, THREAD))
        return body(call(), cost=0.02)

    rig.model.respond = respond
    chat = rig.chat()
    await rig.run(rig.job(chat))
    assert stopped == [1] and len(rig.model.requests) == 1
    assert rig.slack.updates[-1]["text"] == "Stopped."
    (row,) = rig.job_rows()
    assert (row["status"], row["error"], row["answer"]) == ("interrupted", "Stopped from Slack", "")
    assert row["cost_usd"] == 0.02
    assert rig.history(chat) == []
    assert len(rig.slack.posts) == 1


async def test_running_lists_labels_per_thread(slack_rig):
    rig = slack_rig
    seen = []

    def respond(request):
        owner = rig.scope.owner
        seen.append((rig.runner.running(owner, CHANNEL), rig.runner.running(owner, CHANNEL, THREAD),
                     rig.runner.running(owner, CHANNEL, "1.2"), rig.runner.stop(owner, CHANNEL, "1.2")))
        return body()

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))
    assert seen == [(["Gemini"], ["Gemini"], [], 0)]
    assert rig.runner.running(rig.scope.owner, CHANNEL) == []


async def test_job_row_attribution(slack_rig):
    rig = slack_rig
    await rig.run(rig.job(rig.chat(), user_id="U777"))
    (row,) = rig.job_rows()
    assert (row["slack_user_id"], row["slack_channel_id"]) == ("U777", CHANNEL)
    assert row["slack_working_ts"] == rig.slack.posts[0]["ts"]
    assert row["process_id"] == "proc-test"


async def test_answer_escaped(slack_rig):
    """Review Focus 4, end to end: hostile model text can't ping, link or leak the key."""
    rig = slack_rig
    hostile = f"<!channel> look <@U1> <https://evil.example|click> key {KEY} & more"
    rig.model.respond = lambda request: body(answer_item(hostile))
    await rig.run(rig.job(rig.chat()))
    posted = rig.texts()[-1]
    assert "<" not in posted and ">" not in posted and KEY not in posted
    assert posted.startswith("&lt;!channel&gt; look &lt;@U1&gt; &lt;https://evil.example|click&gt;")
    assert "&amp; more" in posted
    assert KEY not in rig.job_rows()[0]["answer"]
