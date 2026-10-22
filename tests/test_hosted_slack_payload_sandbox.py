"""The sandbox folders and rules the Slack bot's instructions share with student advisor chats."""

from openrouter_image_mcp.advisor_payload import SANDBOX_SAFETY
from openrouter_image_mcp.hosted.slack.payload import build_slack_payload
from tests.test_hosted_slack_payload import build, make_chat, make_row

FILES_UPLOAD = {"kind": "files", "id": "file-abcdefgh12345678", "filename": "session/files.json"}


def flat(text: str) -> str:
    return " ".join(text.split())


def with_files() -> str:
    return build_slack_payload(make_chat(True), "q", [], [FILES_UPLOAD], row=make_row(), domains=[],
                               memory_text="", compare=False)["instructions"]


def test_sandbox_names_the_folders_openrouter_saves():
    text = flat(build(sandbox=True)["instructions"])
    assert "/workspace/home/work/" in text and "/tmp/sidecar/scratch/" in text
    assert "/workspace/home/outputs/" in text and "30 days" in text and "5 idle minutes" in text
    assert "Leave those two folders alone" in text


def test_no_files_turn_resets_the_inputs_folder():
    text = flat(build(sandbox=True)["instructions"])
    assert "when none are attached this turn, remove /tmp/sidecar/inputs if it exists" in text
    assert flat(SANDBOX_SAFETY) in text


def test_files_turn_points_at_the_new_inputs_folder_and_skips_unsafe_paths():
    text = flat(with_files())
    assert "/tmp/sidecar/inputs/<path>" in text and "absolute or has a .. segment" in text
    assert "Keep work to reuse in /workspace/home/work/." in text


def test_no_slack_instruction_promises_project_folders_or_persistence_between_turns():
    for text in (build(sandbox=True)["instructions"], with_files(), build()["instructions"],
                 build(compare=True)["instructions"]):
        assert "/workspace/project" not in text and "persists between turns" not in text


def test_slack_bot_is_not_told_to_keep_final_answers_out_of_files():
    for text in (build(sandbox=True)["instructions"], with_files()):
        assert "final answer into" not in text and "task briefs" not in text
