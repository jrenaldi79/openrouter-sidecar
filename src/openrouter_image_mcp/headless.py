"""Headless advisor: a stateful subagent the parent model calls and reads directly.

Unlike the viewer flow (answer stays in the MCP App), ask_advisor returns the answer
to the caller, wrapped as untrusted data. A chat_id restores the transcript and the
session files on later calls, from any client signed in as the same person.
"""

from __future__ import annotations

from .advisor_payload import envelope
from .advisor_results import ACTIVE
from .advisor_store import AdvisorAccessError
from .errors import BadRequestError, OpenRouterError

DEFAULT_WAIT_S = 25
MAX_WAIT_S = 45
PROGRESS_S = 10.0
NEXT_STEP = "Still running. Call wait_advisor_job with this job_id to keep waiting."


def check_wait(wait_s) -> float:
    if wait_s is None:
        return float(DEFAULT_WAIT_S)
    if type(wait_s) not in (int, float) or not 0 <= wait_s <= MAX_WAIT_S:
        raise BadRequestError(f"wait_s must be a number from 0 to {MAX_WAIT_S}.")
    return float(wait_s)


def shape(row: dict) -> dict:
    base = {"status": row["status"], "chat_id": row["chat_id"], "job_id": row["id"]}
    if row["status"] == "completed":
        return base | {"model": row["model"], "answer": envelope(row["answer"]),
                       "cost_usd": row["cost_usd"]}
    if row["status"] in ACTIVE:
        return base | {"status": "running", "next": NEXT_STEP}
    return base | {"error": row["error"]}


class HeadlessAdvisor:
    def __init__(self, service, jobs, preferences=None):
        self.service, self.jobs, self.preferences = service, jobs, preferences

    async def ask(self, prompt, *, chat_id=None, model=None, title=None, sandbox=None,
                  files=None, replace_files=False, skills=None, wait_s=None, progress=None,
                  images=None, search=False) -> dict:
        wait = check_wait(wait_s)
        if chat_id is None:
            chat_id = await self._create(model, title, sandbox)
            created = True
        elif model is not None or title is not None or sandbox is not None:
            raise BadRequestError("model, title and sandbox apply only when creating a session; "
                                  "omit them when passing chat_id.")
        else:
            created = False
        try:
            started = await self.jobs.start(chat_id, prompt, skills, files=files,
                                            replace_files=replace_files, images=images,
                                            search=search)
        except OpenRouterError as error:
            if created:
                self.service.delete(chat_id)  # never leave an empty session behind
                raise
            if isinstance(error, AdvisorAccessError):
                running = self.jobs.active_job(chat_id)
                if running:
                    return {"status": "busy", "chat_id": chat_id, "running_job_id": running}
            raise
        return await self.wait(started["job_id"], wait, progress)

    async def _create(self, model, title, sandbox) -> str:
        resolved = (await self.preferences.resolve(model) if self.preferences is not None
                    else {"model": model})
        started = await self.service.start(resolved["model"], title or "Headless advisor",
                                           sandbox=sandbox)
        return started["chat_id"]

    async def wait(self, job_id, wait_s=None, progress=None) -> dict:
        row = await self.jobs.wait(job_id, check_wait(wait_s), progress, PROGRESS_S)
        return shape(row)
