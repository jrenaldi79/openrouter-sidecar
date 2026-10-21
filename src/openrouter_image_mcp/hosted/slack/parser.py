"""Turns the text of an ``@sidecar`` mention into an intent. Pure: no I/O, no Slack imports."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

ThreadName = Literal["restart", "status", "stop"]
_THREAD_NAMES = ("restart", "status", "stop")
_COMMAND_WORDS = frozenset({"ask", "compare", "models", "set", "usage", "remember", "forget", "help"})
_RECALL = "what do you remember"
_REMEMBER_PREFIX = "remember for this channel:"
_LINK_SCHEMES = ("http://", "https://", "mailto:")
# Sandbox triggers are matched on whole words or stems, never substrings: "paragraph" is not "graph".
_ACTION_STEMS = ("chart", "plot", "graph", "analy", "calculat", "comput", "visuali")
_ACTION_VERBS = frozenset({"run", "execute"})
_CODE_WORDS = frozenset({"code", "script", "python", "program"})
_DATA_WORDS = frozenset({"data", "dataset", "csv", "spreadsheet", "numbers", "table"})
# The caller passes only a flag, not the file name, so recognise the usual text-file extensions.
_TEXT_EXTENSIONS = frozenset({"csv", "tsv", "txt", "json", "md", "log", "xml", "yaml", "yml"})


@dataclass(frozen=True)
class Ask:
    aliases: tuple[str, ...]
    question: str


@dataclass(frozen=True)
class Compare:
    question: str


@dataclass(frozen=True)
class Models:
    pass


@dataclass(frozen=True)
class SetDefault:
    alias: str


@dataclass(frozen=True)
class Usage:
    pass


@dataclass(frozen=True)
class Remember:
    note: str


@dataclass(frozen=True)
class Recall:
    pass


@dataclass(frozen=True)
class Forget:
    n: int


@dataclass(frozen=True)
class Help:
    pass


@dataclass(frozen=True)
class ThreadCommand:
    name: ThreadName


@dataclass(frozen=True)
class Empty:
    pass


Intent = Ask | Compare | Models | SetDefault | Usage | Remember | Recall | Forget | Help | ThreadCommand | Empty


# --- text normalisation ---------------------------------------------------------------


def _unwrap_links(text: str) -> str:
    """``<https://x|label>`` becomes ``https://x``; other ``<...>`` (mentions) are left alone."""
    out: list[str] = []
    pos = 0
    while (start := text.find("<", pos)) != -1:
        end = text.find(">", start)
        if end == -1:
            break
        inner = text[start + 1 : end]
        out.append(text[pos:start])
        out.append(inner.split("|", 1)[0] if inner.startswith(_LINK_SCHEMES) else text[start : end + 1])
        pos = end + 1
    out.append(text[pos:])
    return "".join(out)


def decode_entities(text: str) -> str:
    # Slack escapes only these three; `&amp;` goes last so `&amp;lt;` decodes once.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _starts_with_command(words: str) -> bool:
    first = words.split(None, 1)[0].lower() if words else ""
    return first in _COMMAND_WORDS or first.startswith("!") or words.lower().startswith(_RECALL)


def command_text(text: str, bot_user_id: str) -> str:
    """The words to parse: the command after the first bot mention, else the text as a question."""
    text = _unwrap_links(text)
    mention = f"<@{bot_user_id}>"
    at = text.find(mention)
    if at == -1:
        return decode_entities(text).strip()
    before, after = text[:at].strip(), text[at + len(mention) :].strip()
    if _starts_with_command(after):
        return decode_entities(after)
    return decode_entities(f"{before} {after}" if before and after else before or after)


# --- one small parser per command -----------------------------------------------------


def _take_word(text: str) -> tuple[str, str]:
    """The first whitespace-delimited word and the rest (stripped); both empty if there is none."""
    parts = text.split(None, 1)
    return (parts[0], parts[1].strip()) if len(parts) == 2 else (parts[0] if parts else "", "")


def _alias(word: str) -> str:
    return word if "/" in word else word.lower()


def _parse_ask(rest: str, aliases: Collection[str]) -> Ask:
    known = {a.lower() for a in aliases}
    first, rest = _take_word(rest)
    if not first:
        return Ask((), "")
    chosen = [_alias(first)]
    while True:
        joiner, after = _take_word(rest)
        name, remainder = _take_word(after)
        if joiner.lower() != "and" or not name or not (name.lower() in known or "/" in name):
            break
        if _alias(name) not in chosen:
            chosen.append(_alias(name))
        rest = remainder
    return Ask(tuple(chosen), rest)


def _parse_set(words: list[str], text: str) -> Intent:
    if len(words) == 3 and words[1].lower() == "default":
        return SetDefault(_alias(words[2]))
    return Ask((), text)


def _parse_remember(text: str) -> Intent:
    if text.lower().startswith(_REMEMBER_PREFIX):
        note = text[len(_REMEMBER_PREFIX) :].strip()
        if note:
            return Remember(note)
    return Ask((), text)


def _parse_forget(words: list[str], text: str) -> Intent:
    # isascii: str.isdigit() accepts characters like "²" that int() rejects.
    if len(words) == 2 and words[1].isascii() and words[1].isdigit() and int(words[1]) >= 1:
        return Forget(int(words[1]))
    return Ask((), text)


def _is_recall(text: str) -> bool:
    return text.lower().rstrip().removesuffix("?").rstrip() == _RECALL


def _parse_bang(word: str) -> ThreadCommand | None:
    name = word.lower().removeprefix("!")
    if word.startswith("!") and name in _THREAD_NAMES:
        return ThreadCommand(name)  # type: ignore[arg-type]
    return None


def parse(text: str, aliases: Collection[str]) -> Intent:
    """The intent behind ``text`` (as returned by ``command_text``). Anything unrecognised is a question."""
    text = text.strip()
    if not text:
        return Empty()
    words = text.split()
    first, rest = words[0].lower(), text[len(words[0]) :]
    if (bang := _parse_bang(words[0])) is not None:
        return bang
    if _is_recall(text):
        return Recall()
    if first == "ask":
        return _parse_ask(rest, aliases)
    if first == "compare":
        return Compare(rest.strip())
    if first == "set":
        return _parse_set(words, text)
    if first == "remember":
        return _parse_remember(text)
    if first == "forget":
        return _parse_forget(words, text)
    # Bare commands only: "help me write this" is a question, not the help command.
    bare = {"models": Models, "usage": Usage, "help": Help}
    if len(words) == 1 and first in bare:
        return bare[first]()
    return Ask((), text)


def _is_action(token: str) -> bool:
    if token in _ACTION_VERBS:
        return True
    return token.startswith(_ACTION_STEMS) and not token.startswith("graphic")


def wants_sandbox(question: str, has_text_file: bool) -> bool:
    """Whether the question needs code execution rather than a plain answer."""
    lowered = question.lower()
    if "use sandbox" in lowered:
        return True
    tokens = re.findall(r"[a-z]+", lowered)
    if has_text_file:
        return "attached" in tokens or any(t in _TEXT_EXTENSIONS or _is_action(t) for t in tokens)
    words = set(tokens)
    if words & _ACTION_VERBS and words & _CODE_WORDS:
        return True
    return any(_is_action(t) for t in tokens) and bool(words & _DATA_WORDS)
