"""Offline streaming/jobs security and lifecycle tests against real SQLite."""

import asyncio
import importlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openrouter_image_mcp import keystore
from openrouter_image_mcp.advisor import AdvisorService, SkillPacket
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_store import AdvisorAccessError, AdvisorStore
from openrouter_image_mcp.errors import BadRequestError, OpenRouterError

WORKSPACE = "jobs-workspace"
KEY_A = "sk-or-v1-jobs-account-a"
KEY_B = "sk-or-v1-jobs-account-b"
MODEL = "openai/test-advisor"


def test_jobs_backend_is_available():
    assert importlib.util.find_spec("openrouter_image_mcp.advisor_jobs"), "AdvisorJobs missing"


def event(kind, **fields):
    return ("data: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


def final(text="Final advice", status="completed", container=None):
    body = {"status": status, "output": [{"type": "message", "role": "assistant",
            "phase": "final_answer", "status": "completed", "content": [
                {"type": "output_text", "text": text}]}], "usage": {"cost": 0.002}}
    if container:
        body["output"].append({"type": "openrouter:shell", "container_id": container})
    return body


class SyntheticStream(httpx.AsyncByteStream):
    def __init__(self, state):
        self.state = state

    async def __aiter__(self):
        yield event("response.output_item.added", output_index=0, item={
            "type": "message", "role": "assistant", "phase": "final_answer"})
        yield event("response.reasoning_text.delta", delta="SECRET_REASONING")
        yield event("response.output_text.delta", output_index=0, delta=self.state["partial"])
        self.state["entered"].set()
        try:
            await self.state["release"].wait()
            if self.state.get("raw"):
                for raw in self.state["raw"]:
                    yield raw
            elif self.state.get("malformed"):
                yield b"data: invalid JSON\n\n"
            elif self.state.get("oversized"):
                yield b"data: " + b"x" * 300_000 + b"\n\n"
            elif not self.state.get("no_final"):
                yield event("response.completed", response=final(
                    self.state["answer"], self.state.get("final_status", "completed"),
                    self.state.get("container")))
        finally:
            self.state["cancelled"] += 1



def _sqlite_only(store):
    if store.path is None:
        pytest.skip("inspects the local SQLite file or local export")


@pytest.fixture
async def rig(tmp_path, memory_keyring, db_target):
    assert importlib.util.find_spec("openrouter_image_mcp.advisor_jobs"), "AdvisorJobs missing"
    jobs_type = importlib.import_module("openrouter_image_mcp.advisor_jobs").AdvisorJobs
    keystore.set_key(KEY_A, workspace_id=WORKSPACE)
    state = {"partial": "Partial advice", "answer": "Final advice", "entered": asyncio.Event(),
             "release": asyncio.Event(), "cancelled": 0, "seen": []}

    async def provider(request):
        state["seen"].append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL, "created": int(time.time()) - 86400,
                "architecture": {"output_modalities": ["text"]},
                "supported_parameters": ["tools"]}]})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"id": "or_file_12345678"})
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, stream=SyntheticStream(state), headers={
            "content-type": "text/event-stream"})

    client = AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(provider))
    service = AdvisorService(WORKSPACE, AdvisorStore(db_target), client)
    chat = await service.start(MODEL, "Critic")
    jobs = jobs_type(service)
    try:
        yield jobs, service, chat["chat_id"], state
    finally:
        await jobs.close()
        await client.aclose()


async def terminal(jobs, job_id):
    for _ in range(100):
        status = jobs.status(job_id)
        if status["status"] not in ("queued", "running"):
            return status
        await asyncio.sleep(0.01)
    pytest.fail("Job did not settle")


async def test_background_stream_status_and_final_history_and_original_prompt(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    status = await jobs.start(chat_id, "Original prompt", [SkillPacket(name="guide", content="SKILL_SECRET")])
    assert status["status"] == "queued"
    assert "prompt" not in status and "answer" not in status
    await state["entered"].wait()
    view = jobs.view(status["job_id"])
    assert view["answer"] == "Partial advice" and view["partial"] is True
    assert view["prompt"] == "Original prompt"
    assert "SECRET_REASONING" not in json.dumps(view) and "SKILL_SECRET" not in json.dumps(view)
    assert service.get(chat_id)["messages"] == []
    state["release"].set()
    result = await terminal(jobs, status["job_id"])
    assert result["status"] == "completed" and result["cost_usd"] == 0.002
    assert jobs.view(status["job_id"])["answer"] == "Final advice"
    assert service.get(chat_id)["messages"][-1]["content"] == "Final advice"
    assert KEY_A not in service.store.path.read_bytes().decode(errors="ignore")


@pytest.mark.parametrize("operation", ["status", "view", "export", "summarize"])
async def test_other_credential_known_job_cannot_read_or_act(rig, operation):
    jobs, _, chat_id, state = rig
    job = await jobs.start(chat_id, "Private prompt")
    keystore.set_key(KEY_B, workspace_id=WORKSPACE)
    before = len(state["seen"])
    with pytest.raises(AdvisorAccessError, match="unavailable"):
        result = getattr(jobs, operation)(job["job_id"])
        if operation == "summarize":
            await result
    assert len(state["seen"]) == before


async def test_credential_switch_before_worker_blocks_provider_and_suppresses_partial(rig):
    jobs, service, chat_id, state = rig
    job = await jobs.start(chat_id, "Prompt")
    keystore.set_key(KEY_B, workspace_id=WORKSPACE)
    await asyncio.sleep(0.05)
    assert not any(r.url.path.endswith("/responses") for r in state["seen"])
    keystore.set_key(KEY_A, workspace_id=WORKSPACE)
    assert (await terminal(jobs, job["job_id"]))["status"] == "interrupted"
    assert jobs.view(job["job_id"])["answer"] == ""
    assert service.get(chat_id)["messages"] == []


async def test_credential_switch_during_stream_cannot_save_answer(rig):
    jobs, service, chat_id, state = rig
    job = await jobs.start(chat_id, "Prompt")
    await state["entered"].wait()
    keystore.set_key(KEY_B, workspace_id=WORKSPACE)
    state["release"].set()
    await asyncio.sleep(0.05)
    keystore.set_key(KEY_A, workspace_id=WORKSPACE)
    assert (await terminal(jobs, job["job_id"]))["status"] == "interrupted"
    assert jobs.view(job["job_id"])["answer"] == ""
    assert service.get(chat_id)["messages"] == []


@pytest.mark.parametrize("flag,value", [("no_final", True), ("malformed", True),
    ("oversized", True), ("final_status", "incomplete"), ("container", "foreign")])
async def test_invalid_stream_retains_labelled_partial_without_completed_turn(rig, flag, value):
    jobs, service, chat_id, state = rig
    state[flag] = value
    job = await jobs.start(chat_id, "Prompt")
    state["release"].set()
    status = await terminal(jobs, job["job_id"])
    assert status["status"] == "failed"
    view = jobs.view(job["job_id"])
    assert view["answer"] == "Partial advice" and view["partial"] is True
    assert service.get(chat_id)["messages"] == []
    assert sum(r.url.path.endswith("/responses") for r in state["seen"]) == 1


async def test_total_stream_timeout_cancels_and_does_not_retry(rig):
    jobs, service, chat_id, state = rig
    service.client._timeout_s = 0.01
    job = await jobs.start(chat_id, "Prompt")
    status = await terminal(jobs, job["job_id"])
    assert status["status"] == "failed" and "billed" in status["error"]
    assert state["cancelled"] == 1
    assert jobs.view(job["job_id"])["partial"] is True
    assert service.get(chat_id)["messages"] == []


async def test_shutdown_interrupts_partial_reconnects_without_replay(rig):
    jobs, service, chat_id, state = rig
    job = await jobs.start(chat_id, "Prompt")
    await state["entered"].wait()
    await jobs.close()
    assert jobs.status(job["job_id"])["status"] == "interrupted"
    assert jobs.view(job["job_id"])["answer"] == "Partial advice"
    replacement = type(jobs)(service)
    try:
        assert replacement.status(job["job_id"])["status"] == "interrupted"
        assert sum(r.url.path.endswith("/responses") for r in state["seen"]) == 1
    finally:
        await replacement.close()


async def test_live_job_and_chat_lease_exclusive_across_managers_stale_recovery(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    replacement = type(jobs)(service)
    try:
        job = await jobs.start(chat_id, "Prompt")
        await state["entered"].wait()
        assert replacement.status(job["job_id"])["status"] == "running"
        with pytest.raises(AdvisorAccessError):
            await replacement.start(chat_id, "Duplicate")
        with pytest.raises(AdvisorAccessError):
            await service.send(chat_id, "Competing old API")
        with sqlite3.connect(service.store.path) as db:
            db.execute("UPDATE advisor_jobs SET heartbeat_until=? WHERE id=?", (time.time() - 1, job["job_id"]))
        assert replacement.status(job["job_id"])["status"] == "interrupted"
        # A suspended old worker cannot commit when resumed after expiry.
        state["release"].set()
        await asyncio.sleep(0.05)
        assert service.get(chat_id)["messages"] == []
    finally:
        await replacement.close()


async def test_summary_new_job_preserves_original_and_export_fixed_contained_path(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    await terminal(jobs, job["job_id"])
    exported = jobs.export(job["job_id"])
    path = Path(exported["path"]).resolve()
    assert path.is_relative_to(service.store.path.parent.resolve())
    assert path.name == job["job_id"] + ".md"
    assert "Final advice" in path.read_text(encoding="utf-8")
    summary = await jobs.summarize(job["job_id"])
    assert summary["kind"] == "summary" and summary["source_job_id"] == job["job_id"]
    assert summary["model"] == MODEL and "answer" not in summary
    await terminal(jobs, summary["job_id"])
    assert jobs.view(job["job_id"])["prompt"] == "Prompt"
    assert jobs.view(job["job_id"])["answer"] == "Final advice"
    request = [r for r in state["seen"] if r.url.path.endswith("/responses")][-1]
    assert "concise" in json.loads(request.content)["input"][-1]["content"].lower()


async def test_export_rejects_symlink_destination_escape(rig, tmp_path):
    _sqlite_only(rig[1].store)
    jobs, _, chat_id, state = rig
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    await terminal(jobs, job["job_id"])
    export = jobs.export(job["job_id"])
    path = Path(export["path"])
    path.unlink()
    escape = tmp_path / "escaped.md"
    try:
        path.symlink_to(escape)
    except OSError:
        pytest.skip("Symlink creation requires Windows developer-mode privilege")
    with pytest.raises(OpenRouterError):
        jobs.export(job["job_id"])
    assert not escape.exists()


async def test_close_immediately_after_enqueue_persists_interruption_without_provider(rig):
    jobs, _, chat_id, state = rig
    job = await jobs.start(chat_id, "Prompt")
    await jobs.close()
    assert jobs.status(job["job_id"])["status"] == "interrupted"
    assert not any(r.url.path.endswith("/responses") for r in state["seen"])


async def test_reopen_metadata_and_delete_remove_jobs_preserve_export(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    state["release"].set()
    job = await jobs.start(chat_id, "Private prompt")
    await terminal(jobs, job["job_id"])
    path = Path(jobs.export(job["job_id"])["path"])
    rows = jobs.for_chat(chat_id)
    assert [row["job_id"] for row in rows] == [job["job_id"]]
    assert "answer" not in rows[0] and "prompt" not in rows[0]
    keystore.set_key(KEY_B, workspace_id=WORKSPACE)
    with pytest.raises(AdvisorAccessError):
        jobs.for_chat(chat_id)
    with pytest.raises(AdvisorAccessError):
        jobs.delete_chat(chat_id)
    keystore.set_key(KEY_A, workspace_id=WORKSPACE)
    assert jobs.delete_chat(chat_id)["deleted"] is True
    with pytest.raises(AdvisorAccessError):
        jobs.view(job["job_id"])
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM advisor_jobs").fetchone()[0] == 0
    assert path.exists()


async def test_shutdown_tolerates_deleted_completed_job_record(rig):
    jobs, _, chat_id, state = rig
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    await terminal(jobs, job["job_id"])
    scope, row = jobs._owned(job["job_id"])
    await asyncio.gather(*list(jobs._tasks.values()))
    jobs.delete_chat(chat_id)
    # Shutdown can capture this record before a completed task's callback runs.
    jobs._records[job["job_id"]] = (scope, row)
    await jobs.close()
    with pytest.raises(AdvisorAccessError):
        jobs.view(job["job_id"])


async def test_partial_credentials_redacted_and_stream_commentary_ignored(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    state["partial"] = "Do not leak " + KEY_A + " or " + KEY_B
    state["malformed"] = True
    job = await jobs.start(chat_id, "Prompt")
    state["release"].set()
    status = await terminal(jobs, job["job_id"])
    serialized = json.dumps(jobs.view(job["job_id"]))
    assert KEY_A not in serialized and KEY_B not in serialized
    assert KEY_A not in service.store.path.read_bytes().decode(errors="ignore")
    assert KEY_B not in service.store.path.read_bytes().decode(errors="ignore")
    assert status["status"] == "failed" and "invalid JSON" not in status["error"]


async def test_same_credential_other_workspace_cannot_reopen_job(rig):
    jobs, service, chat_id, _ = rig
    job = await jobs.start(chat_id, "Prompt")
    keystore.set_key(KEY_A, workspace_id="another-workspace")
    foreign_client = AdvisorClient("another-workspace", 30, transport=httpx.MockTransport(
        lambda request: pytest.fail("Foreign workspace reached provider")))
    foreign = type(jobs)(AdvisorService("another-workspace", service.store, foreign_client))
    try:
        with pytest.raises(AdvisorAccessError):
            foreign.view(job["job_id"])
        with pytest.raises(AdvisorAccessError):
            foreign.delete_chat(chat_id)
    finally:
        await foreign.close()
        await foreign_client.aclose()


async def test_export_write_failure_leaves_no_partial_final_file(rig, monkeypatch):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    await terminal(jobs, job["job_id"])

    def failed_fsync(descriptor):
        raise OSError("Synthetic disk failure")

    with monkeypatch.context() as patch:
        patch.setattr("openrouter_image_mcp.advisor_results.os.fsync", failed_fsync)
        with pytest.raises(OpenRouterError, match="saved locally"):
            jobs.export(job["job_id"])
    assert list(service.store.path.parent.rglob(job["job_id"] + ".md")) == []
    path = Path(jobs.export(job["job_id"])["path"])
    assert "Final advice" in path.read_text(encoding="utf-8")


async def test_export_rejects_reparse_parent_even_without_symlink_privilege(rig, monkeypatch):
    _sqlite_only(rig[1].store)
    jobs, _, chat_id, state = rig
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    await terminal(jobs, job["job_id"])
    real_lstat = Path.lstat

    def reparse_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if path.name == "advisor-exports":
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    jobs.export(job["job_id"])
    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(OpenRouterError, match="linked path"):
        jobs.export(job["job_id"])


async def test_stream_event_header_must_match_json_type(rig):
    jobs, service, chat_id, state = rig
    state["raw"] = [b"event: response.completed\n" + event("response.output_text.delta",
                                                             output_index=0, delta="Wrong")]
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    result = await terminal(jobs, job["job_id"])
    assert result["status"] == "failed"
    assert jobs.view(job["job_id"])["answer"] == "Partial advice"
    assert service.get(chat_id)["messages"] == []


async def test_stream_ignores_commentary_and_shell_text_even_with_output_text_shape(rig):
    jobs, _, chat_id, state = rig
    state["raw"] = [
        event("response.output_item.added", output_index=1, item={"type": "message",
              "role": "assistant", "phase": "commentary"}),
        event("response.output_text.delta", output_index=1, delta="COMMENTARY_TRACE"),
        event("response.output_item.added", output_index=2, item={"type": "openrouter:shell"}),
        event("response.output_text.delta", output_index=2, delta="SHELL_TRACE"),
        event("response.completed", response=final()),
    ]
    state["release"].set()
    job = await jobs.start(chat_id, "Prompt")
    result = await terminal(jobs, job["job_id"])
    assert result["status"] == "completed"
    assert jobs.view(job["job_id"])["answer"] == "Final advice"
    assert "TRACE" not in json.dumps(jobs.view(job["job_id"]))


async def test_stale_recovery_reports_owned_upload_cleanup_and_explicit_cleanup_clears_flag(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    job = await jobs.start(chat_id, "Prompt")
    await state["entered"].wait()
    _, scope = service._credentials()
    with sqlite3.connect(service.store.path) as db:
        token = db.execute("SELECT lease_token FROM advisor_jobs WHERE id=?",
                           (job["job_id"],)).fetchone()[0]
    service.store.record_upload(scope, chat_id, "or_file_stale123", token)
    with sqlite3.connect(service.store.path) as db:
        db.execute("UPDATE advisor_jobs SET heartbeat_until=0 WHERE id=?", (job["job_id"],))
    status = jobs.status(job["job_id"])
    assert status["status"] == "interrupted" and status["cleanup_pending"] is True
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("SELECT cleanup_pending FROM advisor_jobs WHERE id=?",
                          (job["job_id"],)).fetchone()[0] == 1
    state["release"].set()
    await asyncio.sleep(0.05)
    assert service.get(chat_id)["messages"] == []
    assert (await service.cleanup(chat_id))["cleanup_pending"] is False
    assert jobs.status(job["job_id"])["cleanup_pending"] is False
    assert jobs.for_chat(chat_id)[0]["cleanup_pending"] is False
    assert sum(r.url.path.endswith("/responses") for r in state["seen"]) == 1


async def test_separate_process_cannot_steal_live_work_but_recovers_expired_heartbeat(rig):
    _sqlite_only(rig[1].store)
    jobs, service, chat_id, state = rig
    job = await jobs.start(chat_id, "Prompt")
    await state["entered"].wait()
    _, scope = service._credentials()
    script = """
import json, sys
from pathlib import Path
from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorAccessError, AdvisorStore, Scope
store = AdvisorStore(Path(sys.argv[1]))
scope = Scope(sys.argv[2], sys.argv[3])
results = AdvisorResults(store)
status = results.status(results.get(scope, sys.argv[5]))
busy = False
if status['status'] == 'running':
    try:
        results.create(scope, store.get(scope, sys.argv[4]), 'Duplicate', 'second-process')
    except AdvisorAccessError:
        busy = True
print(json.dumps({'status': status['status'], 'busy': busy}))
"""

    def other_process():
        command = [sys.executable, "-c", script, str(service.store.path), scope.owner,
                   scope.workspace, chat_id, job["job_id"]]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
        return json.loads(result.stdout)

    assert await asyncio.to_thread(other_process) == {"status": "running", "busy": True}
    assert jobs.status(job["job_id"])["status"] == "running"
    with sqlite3.connect(service.store.path) as db:
        db.execute("UPDATE advisor_jobs SET heartbeat_until=0 WHERE id=?", (job["job_id"],))
    assert await asyncio.to_thread(other_process) == {"status": "interrupted", "busy": False}
    state["release"].set()
    await asyncio.sleep(0.05)
    assert service.get(chat_id)["messages"] == []
    assert sum(r.url.path.endswith("/responses") for r in state["seen"]) == 1


async def test_admission_hook_runs_before_any_job_or_provider_call(rig):
    jobs, _service, chat_id, state = rig
    seen = []

    def refuse(scope):
        seen.append(scope)
        raise BadRequestError("rate_limited: synthetic")

    jobs.admit = refuse
    with pytest.raises(BadRequestError, match="rate_limited"):
        await jobs.start(chat_id, "Brief")
    assert seen and seen[0].workspace == WORKSPACE
    assert jobs.for_chat(chat_id) == []
    assert not [r for r in state["seen"] if r.url.path.endswith("/responses")]
