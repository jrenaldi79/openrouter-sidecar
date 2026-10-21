from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.hosted.slack.threads import ThreadChat, ThreadMap

A = Scope("slack|T1:C1", "ws")
B = Scope("slack|T1:C2", "ws")


def threads(db_target, now=lambda: 100.0):
    found = ThreadMap(db_target)
    found.clock = now
    return found


def test_put_get_roundtrip(db_target):
    tm = threads(db_target)
    assert tm.get(A, "C1", "1.0", "gemini", "inline") is None
    tm.put(A, "C1", "1.0", "gemini", "inline", "chat-1")
    assert tm.get(A, "C1", "1.0", "gemini", "inline") == ThreadChat("chat-1", "gemini", "inline", None)


def test_mode_is_part_of_key(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "chat-i")
    tm.put(A, "C1", "1.0", "gemini", "sandbox", "chat-s")
    assert tm.get(A, "C1", "1.0", "gemini", "inline").chat_id == "chat-i"
    assert tm.get(A, "C1", "1.0", "gemini", "sandbox").chat_id == "chat-s"
    assert {c.chat_id for c in tm.chats_in(A, "C1", "1.0")} == {"chat-i", "chat-s"}


def test_put_replaces_active_row_and_resets_seen(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "old")
    tm.seen(A, "old", "5.5")
    tm.put(A, "C1", "1.0", "gemini", "inline", "new")
    assert tm.get(A, "C1", "1.0", "gemini", "inline") == ThreadChat("new", "gemini", "inline", None)
    assert len(tm.chats_in(A, "C1")) == 1


def test_seen_only_moves_forward(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "c")
    tm.seen(A, "c", "1700000000.000200")
    tm.seen(A, "c", "1700000000.000100")
    assert tm.get(A, "C1", "1.0", "gemini", "inline").last_seen_ts == "1700000000.000200"
    tm.seen(A, "c", "1700000009.000001")
    assert tm.get(A, "C1", "1.0", "gemini", "inline").last_seen_ts == "1700000009.000001"


def test_last_answered_picks_latest(db_target):
    clock = [10.0]
    tm = threads(db_target, lambda: clock[0])
    tm.put(A, "C1", "1.0", "gemini", "inline", "c-i")
    tm.put(A, "C1", "1.0", "gemini", "sandbox", "c-s")
    assert tm.last_answered(A, "C1", "1.0") is None
    tm.answered(A, "c-i")
    clock[0] = 20.0
    tm.answered(A, "c-s")
    assert tm.last_answered(A, "C1", "1.0").chat_id == "c-s"
    clock[0] = 30.0
    tm.answered(A, "c-i")
    assert tm.last_answered(A, "C1", "1.0").chat_id == "c-i"
    assert tm.last_answered(A, "C1", "2.0") is None


def test_archive_hides_and_allows_new(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "c1")
    tm.put(A, "C1", "1.0", "grok", "inline", "c2")
    tm.put(A, "C1", "2.0", "gemini", "inline", "other")
    tm.answered(A, "c1")
    assert tm.archive(A, "C1", "1.0") == 2
    assert tm.get(A, "C1", "1.0", "gemini", "inline") is None
    assert tm.last_answered(A, "C1", "1.0") is None
    assert tm.chats_in(A, "C1", "1.0") == []
    assert tm.get(A, "C1", "2.0", "gemini", "inline").chat_id == "other"
    tm.put(A, "C1", "1.0", "gemini", "inline", "c3")
    assert tm.get(A, "C1", "1.0", "gemini", "inline").chat_id == "c3"
    assert tm.archive(A, "C1", "9.9") == 0


def test_chats_in_channel_and_thread(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "c1")
    tm.put(A, "C1", "2.0", "gemini", "inline", "c2")
    assert [c.chat_id for c in tm.chats_in(A, "C1")] == ["c1", "c2"]
    assert [c.chat_id for c in tm.chats_in(A, "C1", "2.0")] == ["c2"]


def test_threads_scoped_by_principal(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "chat-a")
    tm.answered(A, "chat-a")
    assert tm.get(B, "C1", "1.0", "gemini", "inline") is None
    assert tm.last_answered(B, "C1", "1.0") is None
    assert tm.chats_in(B, "C1") == []
    assert tm.archive(B, "C1", "1.0") == 0
    tm.seen(B, "chat-a", "9.0")
    tm.put(B, "C1", "1.0", "gemini", "inline", "chat-b")
    assert tm.get(A, "C1", "1.0", "gemini", "inline") == ThreadChat("chat-a", "gemini", "inline", None)


def test_workspace_alone_does_not_scope(db_target):
    tm = threads(db_target)
    tm.put(A, "C1", "1.0", "gemini", "inline", "chat-a")
    other_workspace = Scope(A.owner, "other-ws")
    assert tm.get(other_workspace, "C1", "1.0", "gemini", "inline") is None
