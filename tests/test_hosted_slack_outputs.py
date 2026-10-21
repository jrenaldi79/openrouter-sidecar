"""Sandbox outputs: which files the shell results name, and posting them to the thread."""

import httpx
import pytest
from helpers import WS_2027
from slack_fakes import FakeSlack

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.errors import BadRequestError, ProviderError
from openrouter_image_mcp.hosted.slack.outputs import (
    MAX_BYTES,
    MAX_FILES,
    OutputRef,
    Outputs,
    output_refs,
)

BOX = "nu_abc"
ROOT = "/workspace/project/"
FETCH = "it could not be fetched from the sandbox."


def entry(file_id, filename, container=BOX):
    return {"type": "container_file_citation", "container_id": container, "file_id": file_id,
            "filename": filename}


def shell(*files, kind="openrouter:shell"):
    return {"type": kind, "id": "sh_1", "status": "completed", "container_id": BOX, "files": list(files)}


def refs(*files, **kwargs):
    return output_refs([shell(*files, **kwargs)], BOX)


# ---- output_refs ----

def test_only_outputs_folder():
    found = refs(entry("f1", ROOT + "outputs/chart.png"), entry("f2", ROOT + "src/main.py"),
                 entry("f3", ROOT + "outputs"), entry("f4", ROOT + "outputs/"),
                 entry("f5", ROOT + "outputs2/x.png"), entry("f6", ROOT + "notes.md"))
    assert found == [OutputRef("f1", "outputs/chart.png")]


@pytest.mark.parametrize("name", [ROOT + "outputs/chart.png", "project/outputs/chart.png",
                                  "outputs/chart.png"])
def test_path_forms(name):
    assert refs(entry("f1", name)) == [OutputRef("f1", "outputs/chart.png")]


@pytest.mark.parametrize("name", [
    "/etc/outputs/x.png", "/outputs/x.png", "/workspace/other/outputs/x.png", "/workspace/outputs/x.png",
    ROOT + "outputs/../secret.txt", "outputs/../../x", "../outputs/x.png", ROOT + "outputs/./x.png",
    ROOT + "outputs//x.png", ROOT + "outputs/a\\b.png", ROOT + "outputs/a\x00b.png",
    ROOT + "outputs/a\nb.png", "outputs\\x.png", ROOT + "outputs/x.png/",
])
def test_traversal_and_odd_paths_rejected(name):
    assert refs(entry("f1", name)) == []


def test_other_container_ignored():
    items = [shell(entry("f1", ROOT + "outputs/a.png", container="nu_other")),
             {**shell(entry("f2", ROOT + "outputs/b.png")), "container_id": "nu_other"},
             shell(entry("f3", ROOT + "outputs/c.png"))]
    assert output_refs(items, BOX) == [OutputRef("f3", "outputs/c.png")]


def test_both_shell_item_types_and_other_items_ignored():
    items = [shell(entry("f1", "outputs/a.png"), kind="shell_call_output"),
             shell(entry("f2", "outputs/b.png"), kind="function_call"),
             {"type": "message", "files": [entry("f3", "outputs/c.png")]},
             shell(entry("f4", "outputs/d.png"))]
    assert [r.file_id for r in output_refs(items, BOX)] == ["f1", "f4"]


def test_dedupe_keeps_latest_path_in_first_seen_order():
    items = [shell(entry("f1", "outputs/old.png"), entry("f2", "outputs/b.csv")),
             shell(entry("f1", ROOT + "outputs/new.png"))]
    assert output_refs(items, BOX) == [OutputRef("f1", "outputs/new.png"), OutputRef("f2", "outputs/b.csv")]


def test_malformed_entries_ignored():
    bad = [None, "outputs/x.png", 7, {"file_id": "f1"}, entry(1, "outputs/a.png"), entry("f2", 5),
           {"container_id": BOX, "file_id": "f3", "filename": None}, entry("f4", "outputs/ok.png")]
    assert refs(*bad) == [OutputRef("f4", "outputs/ok.png")]
    assert output_refs([shell(), {"type": "openrouter:shell", "container_id": BOX, "files": "nope"},
                        {"type": "openrouter:shell", "container_id": BOX}, "x", None], BOX) == []


def test_entry_without_citation_type_is_accepted():
    plain = {"container_id": BOX, "file_id": "f1", "filename": "outputs/a.png"}
    assert refs(plain) == [OutputRef("f1", "outputs/a.png")]


# ---- Outputs.post ----

class Client:
    """Stands in for AdvisorClient.container_file."""

    def __init__(self, files=None, errors=None):
        self.files, self.errors, self.calls = files or {}, errors or {}, []

    async def container_file(self, key, container_id, file_id, *, max_bytes):
        self.calls.append({"key": key, "container_id": container_id, "file_id": file_id,
                           "max_bytes": max_bytes})
        if file_id in self.errors:
            raise self.errors[file_id]
        data = self.files[file_id]
        if len(data) > max_bytes:
            raise ProviderError(f"Sandbox file is larger than {max_bytes} bytes.")
        return data


async def post(slack, client, *named):
    found = [OutputRef(file_id, path) for file_id, path in named]
    return await Outputs(slack).post(client, "sk-key", "C1", "1.1", "Gemini", BOX, found)


async def test_posts_each_file_to_the_thread():
    slack, client = FakeSlack(), Client({"f1": b"png", "f2": b"a,b"})
    notes = await post(slack, client, ("f1", "outputs/chart.png"), ("f2", "outputs/sub/data.csv"))
    assert notes == [] and slack.posts == []
    assert [(u["channel"], u["thread_ts"], u["filename"], u["title"], u["content"], u["username"])
            for u in slack.uploads] == [
        ("C1", "1.1", "chart.png", "chart.png", b"png", "Sidecar · Gemini"),
        ("C1", "1.1", "sub_data.csv", "sub_data.csv", b"a,b", "Sidecar · Gemini")]
    assert client.calls[0]["container_id"] == BOX and client.calls[0]["key"] == "sk-key"


async def test_long_names_are_bounded_and_keep_the_extension():
    slack, client = FakeSlack(), Client({"f1": b"x"})
    await post(slack, client, ("f1", "outputs/" + "a" * 300 + ".png"))
    name = slack.uploads[0]["filename"]
    assert len(name) == 200 and name.endswith(".png") and slack.uploads[0]["title"] == name


async def test_caps_five_files_twenty_mb():
    slack = FakeSlack()
    named = [(f"f{i}", f"outputs/p{i}.png") for i in range(MAX_FILES + 1)]
    client = Client({file_id: b"x" for file_id, _ in named})
    notes = await post(slack, client, *named)
    assert len(slack.uploads) == MAX_FILES
    assert notes == ["p5.png wasn't posted: only 5 files are posted per answer."]
    assert [c["file_id"] for c in client.calls] == [f"f{i}" for i in range(MAX_FILES)]  # sixth never downloaded
    assert slack.posts[0]["text"] == notes[0]


async def test_byte_budget_is_shared_and_later_small_files_still_fit():
    slack = FakeSlack()
    client = Client({"f1": b"a" * (MAX_BYTES - 10), "f2": b"b" * 11, "f3": b"c" * 10})
    notes = await post(slack, client, ("f1", "outputs/big.bin"), ("f2", "outputs/over.bin"),
                       ("f3", "outputs/fits.bin"))
    assert [u["filename"] for u in slack.uploads] == ["big.bin", "fits.bin"]
    assert notes == ["over.bin wasn't posted: over the 20 MB limit."]
    assert [c["max_bytes"] for c in client.calls] == [MAX_BYTES, 10, 10]


async def test_download_failure_noted_not_raised():
    slack = FakeSlack()
    client = Client({"f2": b"ok"}, {"f1": ProviderError("Sandbox file download failed (HTTP 500)."),
                                    "f3": BadRequestError("Sandbox file reference is invalid.")})
    notes = await post(slack, client, ("f1", "outputs/a.png"), ("f2", "outputs/b.png"),
                       ("f3", "outputs/c.png"))
    assert [u["filename"] for u in slack.uploads] == ["b.png"]
    assert notes == [f"a.png wasn't posted: {FETCH}", f"c.png wasn't posted: {FETCH}"]
    assert slack.posts[0]["text"] == "\n".join(notes)


async def test_unexpected_download_error_noted_not_raised():
    slack = FakeSlack()
    notes = await post(slack, Client(errors={"f1": RuntimeError("boom sk-key")}), ("f1", "outputs/a.png"))
    assert notes == [f"a.png wasn't posted: {FETCH}"]


async def test_upload_failure_noted_and_next_file_still_posts():
    slack, client = FakeSlack(), Client({"f1": b"1", "f2": b"2"})
    slack.fail_next("upload", "file_uploads_disabled")
    notes = await post(slack, client, ("f1", "outputs/a.png"), ("f2", "outputs/b.png"))
    assert [u["filename"] for u in slack.uploads] == ["b.png"]
    assert notes == ["a.png wasn't posted: Slack would not take the upload."]
    assert "file_uploads_disabled" not in slack.posts[0]["text"]


async def test_notes_have_no_ids_or_error_text():
    slack = FakeSlack()
    client = Client({"file_secret": b"x", "f2": b"y"},
                    {"f2": ProviderError(f"failed for {BOX} file f2 sk-key")})
    slack.fail_next("upload", "bad_token_xoxb")
    notes = await post(slack, client, ("file_secret", "outputs/a.png"), ("f2", "outputs/b.png"),
                       *[(f"x{i}", f"outputs/c{i}.png") for i in range(MAX_FILES)])
    text = "\n".join(notes) + "".join(p["text"] for p in slack.posts)
    for leaked in (BOX, "file_secret", "f2", "x0", "sk-key", "bad_token", "failed for"):
        assert leaked not in text


async def test_note_names_cannot_form_mentions():
    slack, client = FakeSlack(), Client(errors={"f1": ProviderError("no")})
    await post(slack, client, ("f1", "outputs/<!channel>&<@U1>.png"))
    text = slack.posts[0]["text"]
    assert "<" not in text and ">" not in text and "&lt;!channel&gt;" in text
    assert slack.posts[0]["username"] == "Sidecar · Gemini" and slack.posts[0]["thread_ts"] == "1.1"


async def test_post_never_raises_even_when_the_notes_message_fails():
    slack = FakeSlack()
    slack.fail_next("post", "channel_not_found")
    notes = await post(slack, Client(errors={"f1": ProviderError("no")}), ("f1", "outputs/a.png"))
    assert len(notes) == 1 and slack.posts == []


async def test_post_never_raises_on_unexpected_slack_error():
    class Broken(FakeSlack):
        async def upload(self, *args, **kwargs):
            raise RuntimeError("socket closed")

    notes = await post(Broken(), Client({"f1": b"x"}), ("f1", "outputs/a.png"))
    assert notes == ["a.png wasn't posted: Slack would not take the upload."]


async def test_no_refs_makes_no_calls():
    slack, client = FakeSlack(), Client()
    assert await post(slack, client) == []
    assert client.calls == [] and slack.uploads == [] and slack.posts == []


async def test_real_client_over_cap_is_reported_as_the_size_limit():
    """The size note keys off the client's own error, so pin it against the real `container_file`."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "/files/f1/" in request.url.path:
            return httpx.Response(200, content=b"x" * (MAX_BYTES + 1))
        return httpx.Response(500)

    client = AdvisorClient(WS_2027, 30, transport=httpx.MockTransport(handle))
    notes = await post(FakeSlack(), client, ("f1", "outputs/big.bin"), ("f2", "outputs/gone.bin"))
    assert notes == ["big.bin wasn't posted: over the 20 MB limit.", f"gone.bin wasn't posted: {FETCH}"]
