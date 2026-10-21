"""Text attachments on the triggering Slack message, as session files.

Anything that cannot be read as plain UTF-8 text within the limits goes to `listed` (by name) and
never raises: a bad attachment must not stop the answer.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence

from ...session_files import (
    MAX_FILE_CHARS,
    MAX_PATH_CHARS,
    MAX_SESSION_CHARS,
    FilePacket,
)
from .api import SlackApi, SlackCallError, SlackFile

log = logging.getLogger(__name__)

FILES_DIR = "slack/files"
MAX_DOWNLOAD_BYTES = MAX_FILE_CHARS * 4  # UTF-8 is at most 4 bytes per character
TEXT_EXTENSIONS = frozenset({"csv", "md", "txt", "py", "js", "ts", "json", "yaml", "yml", "sql", "html", "css"})
_NAME_ROOM = 8  # keeps "-NN" suffixes inside MAX_PATH_CHARS
MAX_NAME_CHARS = MAX_PATH_CHARS - len(FILES_DIR) - 1 - _NAME_ROOM
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_name(raw: str) -> str:
    """A basename that cannot leave `slack/files/` and fits the session path limit."""
    base = _CONTROL.sub("", (raw or "").replace("\\", "/").rsplit("/", 1)[-1]).strip().lstrip(".").strip()
    if not base:
        return "file"
    if len(base) <= MAX_NAME_CHARS:
        return base
    stem, ext = os.path.splitext(base)
    ext = ext[:16]
    return stem[:MAX_NAME_CHARS - len(ext)] + ext


def is_text_file(file: SlackFile) -> bool:
    ext = os.path.splitext(file.name)[1].lstrip(".").lower()
    return file.mimetype.startswith("text/") or ext in TEXT_EXTENSIONS


def _unique(name: str, taken: set[str]) -> str:
    candidate, (stem, ext), n = name, os.path.splitext(name), 1
    while candidate in taken:
        n += 1
        candidate = f"{stem}-{n}{ext}"
    taken.add(candidate)
    return candidate


async def _read_text(api: SlackApi, file: SlackFile) -> str | None:
    """The file's text, or None when it is too large, not UTF-8 or cannot be downloaded."""
    if file.size > MAX_DOWNLOAD_BYTES:
        return None
    try:
        data = await api.download(file.url, MAX_DOWNLOAD_BYTES)
        text = data.decode("utf-8")
    except SlackCallError as error:
        log.warning("slack attachment not downloaded: %s", error.code)
        return None
    except UnicodeDecodeError:
        log.warning("slack attachment is not UTF-8 text")
        return None
    except Exception as error:  # noqa: BLE001 - a broken attachment must never fail the request
        log.warning("slack attachment skipped: %s", type(error).__name__)
        return None
    return text if len(text) <= MAX_FILE_CHARS else None


async def trigger_files(api: SlackApi, files: Sequence[SlackFile],
                        budget: int = MAX_SESSION_CHARS) -> tuple[list[FilePacket], list[str]]:
    """Packets for the readable text files while their total stays within `budget` characters;
    the rest are listed by name."""
    packets: list[FilePacket] = []
    listed: list[str] = []
    taken: set[str] = set()
    used = 0
    for file in files:
        text = await _read_text(api, file) if is_text_file(file) else None
        if text is None or used + len(text) > budget:
            listed.append(file.name or "file")
            continue
        used += len(text)
        packets.append(FilePacket(path=f"{FILES_DIR}/{_unique(safe_name(file.name), taken)}", content=text))
    return packets, listed
