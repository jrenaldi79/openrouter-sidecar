"""Hosted clients (claude.ai, Desktop) get the workflow guidance the local plugin gives
Claude Code: server instructions, plus the class skills as prompts and resources."""

import re
from pathlib import Path

from mcp.client import advertise

from openrouter_image_mcp.hosted import guidance
from tests.hosted_harness import running_server

ROOT = Path(__file__).resolve().parent.parent
SKILLS = {"openrouter-image", "architectural-render-polish"}
# An MCP Apps client (claude.ai, Desktop) also sees the app-only open_image_uploader.
APPS = [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]
LOCAL_ONLY = ("auth_login", "auth_logout", "mask_path", "output_dir", "absolute", "~/Pictures",
              "saves the results locally", "next to the render")


def test_hosted_skills_are_packaged_with_frontmatter():
    skills = guidance.hosted_skills()
    assert set(skills) == SKILLS
    for name, skill in skills.items():
        assert skill.text.startswith(f"---\nname: {name}\ndescription: ")
        assert skill.description and len(skill.description) <= 1024


def test_hosted_skills_drop_local_file_workflows():
    for skill in guidance.hosted_skills().values():
        for phrase in LOCAL_ONLY:
            assert phrase not in skill.text, (skill.name, phrase)


def test_prompt_template_matches_the_source_skill():
    source = ROOT / "skills" / "architectural-render-polish" / "prompt-template.md"
    [support] = guidance.hosted_skills()["architectural-render-polish"].files.values()
    assert support == source.read_text(encoding="utf-8")


async def test_instructions_prompts_and_resources_reach_clients(db_target, tmp_path):
    async with running_server(db_target, env={"ASSET_DIR": str(tmp_path / "assets")}) as server, \
            server.client(server.token(), extensions=APPS) as client:
        instructions = client.instructions or ""
        listed = (await client.list_tools()).tools
        tools = {tool.name for tool in listed}
        prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        prompt = await client.get_prompt("openrouter-image", {})
        resource = await client.read_resource("skill://architectural-render-polish/prompt-template.md")
    for phrase in ("asset id", "create_image_upload", "chat_id", "files", "skill://", "cost"):
        assert phrase in instructions, phrase
    assert SKILLS <= prompts
    assert {f"skill://{name}" for name in SKILLS} <= resources
    assert prompt.messages[0].content.text == guidance.hosted_skills()["openrouter-image"].text
    assert "KEEP EXACTLY THE SAME" in resource.contents[0].text
    named = set(re.findall(r"`([a-z_]+)\(", instructions))
    for skill in guidance.hosted_skills().values():
        named |= set(re.findall(r"`([a-z_]+)\(", skill.text))
    assert named and named <= tools, sorted(named - tools)


LOCAL_WORDING = re.compile(r"\blocal(ly)?\b|this computer", re.IGNORECASE)


async def test_tool_descriptions_do_not_describe_a_local_install(db_target, tmp_path):
    async with running_server(db_target, env={"ASSET_DIR": str(tmp_path / "assets")}) as server, \
            server.client(server.token(), extensions=APPS) as client:
        listed = (await client.list_tools()).tools
    wrong = {tool.name: LOCAL_WORDING.findall(tool.description or "") for tool in listed
             if tool.name != "create_local_import" and LOCAL_WORDING.search(tool.description or "")}
    assert wrong == {}


# claude.ai keeps what an app adds to context and the model fetches it on demand
# ("check the app on screen"), so the tools must tell Claude to look there.
CHECK_APP = re.compile(r"check the app on screen", re.IGNORECASE)
APP_HANDOFF_TOOLS = {"send_advisor_message", "open_advisor_result", "open_image_uploader"}


async def test_app_handoff_tools_tell_claude_to_check_the_app(db_target, tmp_path):
    async with running_server(db_target, env={"ASSET_DIR": str(tmp_path / "assets")}) as server, \
            server.client(server.token(), extensions=APPS) as client:
        listed = {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}
        opened = await client.call_tool("open_image_uploader", {})
    assert {name for name in APP_HANDOFF_TOOLS if not CHECK_APP.search(listed[name])} == set()
    assert "appears in the conversation" not in listed["open_image_uploader"] + opened.content[0].text
    assert CHECK_APP.search(opened.content[0].text)
    assert "chooses what to send to the parent chat" not in listed["send_advisor_message"]
    assert CHECK_APP.search(guidance.INSTRUCTIONS.replace("\n", " "))
