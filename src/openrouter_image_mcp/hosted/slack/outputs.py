"""Sandbox outputs: the files a sandbox job wrote to `outputs/`, posted to the Slack thread.

`output_refs` reads the `files` of each shell result (`openrouter:shell`, or `shell_call_output`) and keeps
only the team's own container and paths under `outputs/`. `Outputs.post` downloads each file with the team
key and uploads it to the thread. Skipped and failed files get one plain note naming the file; ids, keys and
error text never reach Slack. A failure here never fails the answer, so `post` never raises.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import OpenRouterError
from .api import SlackApi, SlackCallError
from .replies import escape

if TYPE_CHECKING:
    from ...advisor_client import AdvisorClient

log = logging.getLogger(__name__)

MAX_FILES = 5
MAX_BYTES = 20 * 1024 * 1024
MAX_NAME = 200
MAX_EXT = 32
SHELL_TYPES = frozenset({"openrouter:shell", "shell_call_output"})
FIELDS = ("container_id", "file_id", "filename")
OUTPUTS = "outputs"
PROJECT_PREFIXES = ("/workspace/project/", "project/")
TOO_BIG = "larger than"  # what `AdvisorClient.container_file` says when a file is over its cap
_FETCH = "it could not be fetched from the sandbox."
_UPLOAD = "Slack would not take the upload."


@dataclass(frozen=True)
class OutputRef:
    file_id: str
    path: str  # relative to the project folder, always `outputs/<something>`


def normalize(filename: object) -> str | None:
    """`filename` as `outputs/<rel>`, or None for anything else (other folders, traversal, odd characters)."""
    if not isinstance(filename, str) or "\\" in filename or any(ord(c) < 32 or ord(c) == 127 for c in filename):
        return None
    rel = next((filename[len(p):] for p in PROJECT_PREFIXES if filename.startswith(p)), filename)
    if rel.startswith("/"):  # any other absolute path
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts) or parts[0] != OUTPUTS or len(parts) < 2:
        return None
    return rel


def _entries(item: object, container_id: str):
    if not isinstance(item, dict) or item.get("type") not in SHELL_TYPES:
        return
    if item.get("container_id", container_id) != container_id or not isinstance(item.get("files"), list):
        return
    for entry in item["files"]:
        if (isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in FIELDS)
                and entry["container_id"] == container_id):
            yield entry


def output_refs(items: list[dict], container_id: str) -> list[OutputRef]:
    """Files under `outputs/` named by the chat's own shell results; the latest path wins per file id, in
    order of first appearance."""
    found: dict[str, str] = {}
    for item in items:
        for entry in _entries(item, container_id):
            path = normalize(entry["filename"])
            if path is not None:
                found[entry["file_id"]] = path
    return [OutputRef(file_id, path) for file_id, path in found.items()]


def upload_name(path: str) -> str:
    """The path under `outputs/` with `/` as `_`, at most `MAX_NAME` characters, extension kept."""
    name = path[len(OUTPUTS) + 1:].replace("/", "_")
    if len(name) <= MAX_NAME:
        return name
    stem, ext = os.path.splitext(name)
    ext = ext[:MAX_EXT]
    return stem[:MAX_NAME - len(ext)] + ext


def _skipped(name: str, why: str) -> str:
    return f"{name} wasn't posted: {why}"


class Outputs:
    def __init__(self, api: SlackApi):
        self.api = api

    async def post(self, client: AdvisorClient, key: str, channel: str, thread_ts: str, label: str,
                   container_id: str, refs: list[OutputRef]) -> list[str]:
        """Upload what fits and return the notes for the rest (also posted as one message). Never raises."""
        username, notes, budget, sent = f"Sidecar · {label}", [], MAX_BYTES, 0
        try:
            for ref in refs:
                name = upload_name(ref.path)
                if sent >= MAX_FILES:
                    notes.append(_skipped(name, f"only {MAX_FILES} files are posted per answer."))
                    continue
                data, why = await self._fetch(client, key, container_id, ref, name, budget)
                if data is None:
                    notes.append(_skipped(name, why))
                    continue
                if not await self._upload(channel, thread_ts, name, data, username):
                    notes.append(_skipped(name, _UPLOAD))
                    continue
                sent, budget = sent + 1, budget - len(data)
            await self._say(channel, thread_ts, username, notes)
        except Exception as error:  # noqa: BLE001 - outputs must never fail the answer
            log.warning("slack outputs failed: %s", type(error).__name__)
        return notes

    @staticmethod
    async def _fetch(client, key, container_id, ref: OutputRef, name: str, budget: int):
        """(bytes, "") or (None, why); the reason never carries ids or error text."""
        try:
            return await client.container_file(key, container_id, ref.file_id, max_bytes=budget), ""
        except OpenRouterError as error:
            log.warning("slack output download failed: %s", type(error).__name__)
            big = TOO_BIG in getattr(error, "message", "")
            return None, f"over the {MAX_BYTES // 2**20} MB limit." if big else _FETCH
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the rest
            log.warning("slack output download failed: %s", type(error).__name__)
            return None, _FETCH

    async def _upload(self, channel: str, thread_ts: str, name: str, data: bytes, username: str) -> bool:
        try:
            await self.api.upload(channel, thread_ts, name, data, title=name, username=username)
        except SlackCallError as error:
            log.warning("slack output upload failed: %s", error.code)
            return False
        except Exception as error:  # noqa: BLE001 - one bad upload must not stop the rest
            log.warning("slack output upload failed: %s", type(error).__name__)
            return False
        return True

    async def _say(self, channel: str, thread_ts: str, username: str, notes: list[str]) -> None:
        if not notes:
            return
        try:
            await self.api.post(channel, thread_ts, escape("\n".join(notes)), username=username)
        except SlackCallError as error:
            log.warning("slack output notes failed: %s", error.code)
