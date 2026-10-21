"""Sandbox outputs: the files a sandbox job wrote to `outputs/`, posted to the Slack thread.

`output_refs` reads the `files` of each shell result (`openrouter:shell`, or `shell_call_output`) and keeps
only the team's own container and paths under `outputs/`. `Outputs.post` downloads each file with the team
key and uploads it to the thread. Skipped and failed files get one plain note naming the file; ids, keys and
error text never reach Slack. A failure here never fails the answer, so `post` never raises.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import ContainerFileTooLargeError, OpenRouterError
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
MAX_NOTES = 10  # per-file notes in the thread; the rest are summarized in one line
MAX_DOWNLOAD_FAILURES = 3  # consecutive failures before the remaining downloads are skipped
HIDDEN = frozenset({"Cc", "Cf", "Zl", "Zp"})  # controls, bidi and zero-width formatting, line separators
_FETCH = "it could not be fetched from the sandbox."
_BIG = f"it would take this answer past {MAX_BYTES // 2**20} MB of files."
_UPLOAD = "Slack would not take the upload."


@dataclass(frozen=True)
class OutputRef:
    file_id: str
    path: str  # relative to the project folder, always `outputs/<something>`


def normalize(filename: object) -> str | None:
    """`filename` as `outputs/<rel>`, or None for anything else (other folders, traversal, odd characters)."""
    if not isinstance(filename, str) or "\\" in filename:
        return None
    if any(unicodedata.category(c) in HIDDEN for c in filename):
        return None
    rel = next((filename[len(p):] for p in PROJECT_PREFIXES if filename.startswith(p)), filename)
    if rel.startswith("/"):  # any other absolute path
        return None
    parts = rel.split("/")
    if any(not part.strip() or part in (".", "..") for part in parts) or parts[0] != OUTPUTS or len(parts) < 2:
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


class _Notes:
    """At most `MAX_NOTES` per-file notes; the files beyond that are counted for one summary line."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.more = 0

    def add(self, name: str, why: str) -> None:
        if len(self.lines) < MAX_NOTES:
            self.lines.append(_skipped(name, why))
        else:
            self.more += 1

    def finish(self) -> list[str]:
        if self.more:
            noun = "file wasn't" if self.more == 1 else "files weren't"
            self.lines.append(f"\u2026and {self.more} more {noun} posted.")
        return self.lines


class Outputs:
    def __init__(self, api: SlackApi):
        self.api = api

    async def post(self, client: AdvisorClient, key: str, channel: str, thread_ts: str, label: str,
                   container_id: str, refs: list[OutputRef]) -> list[str]:
        """Upload what fits and return the notes for the rest (also posted as one message). Never raises
        (a cancellation still propagates)."""
        username, notes, budget, sent, failures = f"Sidecar · {label}", _Notes(), MAX_BYTES, 0, 0
        try:
            for index, ref in enumerate(refs):
                if failures >= MAX_DOWNLOAD_FAILURES:  # the sandbox is down: don't try the rest
                    notes.more += len(refs) - index
                    break
                name = upload_name(ref.path)
                if sent >= MAX_FILES:
                    notes.add(name, f"only {MAX_FILES} files are posted per answer.")
                    continue
                data, why = await self._fetch(client, key, container_id, ref, budget)
                failures = failures + 1 if why == _FETCH else 0
                if data is None:
                    notes.add(name, why)
                elif not await self._upload(channel, thread_ts, name, data, username):
                    notes.add(name, _UPLOAD)
                else:
                    sent, budget = sent + 1, budget - len(data)
            await self._say(channel, thread_ts, username, notes.finish())
        except Exception as error:  # noqa: BLE001 - outputs must never fail the answer
            log.warning("slack outputs failed: %s", type(error).__name__)
        return notes.lines

    @staticmethod
    async def _fetch(client, key, container_id, ref: OutputRef, budget: int):
        """(bytes, "") or (None, why); the reason never carries ids or error text."""
        try:
            return await client.container_file(key, container_id, ref.file_id, max_bytes=budget), ""
        except ContainerFileTooLargeError:
            return None, _BIG
        except OpenRouterError as error:
            log.warning("slack output download failed: %s", type(error).__name__)
            return None, _FETCH
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
