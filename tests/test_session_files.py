"""Session files: per-chat text files restored on later advisor turns."""

import pytest

from openrouter_image_mcp.advisor_store import AdvisorAccessError, AdvisorStore, Scope
from openrouter_image_mcp.errors import BadRequestError
from openrouter_image_mcp.session_files import FilePacket, SessionFiles

ALICE = Scope("email|alice", "ws-1")
BOB = Scope("email|bob", "ws-1")


@pytest.fixture
def rig(db_target):
    store = AdvisorStore(db_target)
    chat = store.create(ALICE, "test/model", "Chat")
    return store, SessionFiles(store), chat.id


def test_upsert_versions_and_current_sorted(rig):
    _, files, chat_id = rig
    delta = files.upsert(ALICE, chat_id, [FilePacket(path="src/b.py", content="b"),
                                          FilePacket(path="a.md", content="a")])
    assert delta.added == [("a.md", 1), ("src/b.py", 1)] and not delta.updated
    current = files.current(ALICE, chat_id)
    assert [(f["path"], f["version"]) for f in current] == [("a.md", 1), ("src/b.py", 1)]
    assert current[0]["content"] == "a" and len(current[0]["sha256"]) == 64


def test_version_bumps_only_on_changed_content(rig):
    _, files, chat_id = rig
    files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="one")])
    same = files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="one")])
    assert same.empty
    changed = files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="two")])
    assert changed.updated == [("a.py", 2)]
    assert changed.note() == "Updated since last turn: a.py (v2)"


def test_replace_drops_omitted_paths(rig):
    _, files, chat_id = rig
    files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="a"),
                                  FilePacket(path="b.py", content="b")])
    delta = files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="a")], replace=True)
    assert delta.removed == ["b.py"]
    assert [f["path"] for f in files.current(ALICE, chat_id)] == ["a.py"]
    assert "removed b.py" in delta.note()


def test_other_principal_cannot_read_or_write(rig):
    _, files, chat_id = rig
    files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="secret")])
    with pytest.raises(AdvisorAccessError):
        files.current(BOB, chat_id)
    with pytest.raises(AdvisorAccessError):
        files.upsert(BOB, chat_id, [FilePacket(path="a.py", content="x")])


@pytest.mark.parametrize("path", ["/etc/passwd", "../up.py", "a/../../b", "C:\\x.py", "a\\b.py",
                                  "", " ", "a\x00b", "x" * 301, "a/\nb"])
def test_rejects_unsafe_paths(rig, path):
    _, files, chat_id = rig
    with pytest.raises(BadRequestError):
        files.upsert(ALICE, chat_id, [FilePacket(path=path, content="x")])


def test_normalizes_dot_segments(rig):
    _, files, chat_id = rig
    delta = files.upsert(ALICE, chat_id, [FilePacket(path="./src//a.py", content="x")])
    assert delta.added == [("src/a.py", 1)]


@pytest.mark.parametrize("content", ["nul\x00byte", "lone \ud800 surrogate"])
def test_rejects_binary_or_invalid_text(rig, content):
    _, files, chat_id = rig
    # Pydantic may already refuse invalid text at the tool boundary (ValueError).
    with pytest.raises((BadRequestError, ValueError)):
        files.upsert(ALICE, chat_id, [FilePacket(path="a.bin", content=content)])


def test_size_limits(rig):
    _, files, chat_id = rig
    with pytest.raises(BadRequestError, match="100000"):
        files.upsert(ALICE, chat_id, [FilePacket(path="big.txt", content="x" * 100_001)])
    files.upsert(ALICE, chat_id, [FilePacket(path="a.txt", content="x" * 100_000),
                                  FilePacket(path="b.txt", content="x" * 90_000)])
    with pytest.raises(BadRequestError, match="200000"):
        files.upsert(ALICE, chat_id, [FilePacket(path="c.txt", content="x" * 20_000)])
    # A rejected upsert changes nothing.
    assert [f["path"] for f in files.current(ALICE, chat_id)] == ["a.txt", "b.txt"]


def test_duplicate_paths_rejected(rig):
    _, files, chat_id = rig
    with pytest.raises(BadRequestError):
        files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="1"),
                                      FilePacket(path="./a.py", content="2")])


def test_deleting_chat_deletes_its_files(rig):
    store, files, chat_id = rig
    files.upsert(ALICE, chat_id, [FilePacket(path="a.py", content="a")])
    store.delete(ALICE, chat_id)
    other = store.create(ALICE, "test/model", "Chat 2")
    assert files.current(ALICE, other.id) == []
    with pytest.raises(AdvisorAccessError):
        files.current(ALICE, chat_id)


def test_packet_is_strict():
    with pytest.raises(ValueError):
        FilePacket(path="a.py", content="x", mode="755")
