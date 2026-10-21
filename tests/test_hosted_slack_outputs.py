"""Sandbox outputs: which files the shell results name (`output_refs`)."""

import pytest

from openrouter_image_mcp.hosted.slack.outputs import OutputRef, output_refs

BOX = "nu_abc"
ROOT = "/workspace/home/"


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


@pytest.mark.parametrize("name", [ROOT + "outputs/chart.png", "~/outputs/chart.png",
                                  "outputs/chart.png"])
def test_path_forms(name):
    assert refs(entry("f1", name)) == [OutputRef("f1", "outputs/chart.png")]


@pytest.mark.parametrize("name", [
    "/etc/outputs/x.png", "/outputs/x.png", "/workspace/other/outputs/x.png", "/workspace/outputs/x.png", "/workspace/project/outputs/x.png", "project/outputs/x.png",
    ROOT + "outputs/../secret.txt", "outputs/../../x", "../outputs/x.png", ROOT + "outputs/./x.png",
    ROOT + "outputs//x.png", ROOT + "outputs/a\\b.png", ROOT + "outputs/a\x00b.png",
    ROOT + "outputs/a\nb.png", "outputs\\x.png", ROOT + "outputs/x.png/",
])
def test_traversal_and_odd_paths_rejected(name):
    assert refs(entry("f1", name)) == []


@pytest.mark.parametrize("name", [
    ROOT + "outputs/a\u0085b.png",  # C1 control (Cc)
    ROOT + "outputs/gnp.\u202epng",  # right-to-left override (Cf)
    ROOT + "outputs/a\u200bb.png",  # zero-width space (Cf)
    ROOT + "outputs/a\ufeffb.png",  # byte order mark (Cf)
    ROOT + "outputs/a\u2028b.png",  # line separator (Zl)
    ROOT + "outputs/a\u2029b.png",  # paragraph separator (Zp)
    ROOT + "outputs/a\x7fb.png",  # DEL (Cc)
    ROOT + "outputs/ /x.png",  # a segment that is only whitespace
    ROOT + "outputs/\u3000/x.png",
    ROOT + "outputs/\u00a0",
    ROOT + "outputs/   ",
])
def test_unicode_controls_and_blank_segments_rejected(name):
    assert refs(entry("f1", name)) == []


def test_ordinary_unicode_names_are_kept():
    assert refs(entry("f1", "outputs/r\u00e9sum\u00e9 \u56fe.png")) == [OutputRef("f1", "outputs/r\u00e9sum\u00e9 \u56fe.png")]


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
