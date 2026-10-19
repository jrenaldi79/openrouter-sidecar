"""Authenticated import of a local export: one-time PUT link, cohort-bound, idempotent."""

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from openrouter_image_mcp import local_bundle as lb
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.assets import AssetStore, LocalDirBlobs
from openrouter_image_mcp.hosted.imports import LocalImports, import_routes
from openrouter_image_mcp.hosted.uploads import TransferTokens, transfer_routes
from openrouter_image_mcp.session_files import SessionFiles
from tests.hosted_harness import WS_2027, WS_2028, running_server

ORIGIN = "https://sidecar.example"
ALICE = Scope("iss|alice", WS_2027)
BOB = Scope("iss|bob", WS_2027)
PREFS = {"general_default": "openai/gpt-test", "family_defaults": {"google": "google/gemini-test"},
         "onboarding_completed": True}


def bundle(workspace=WS_2027, title="Critic", prefs=PREFS):
    chat = {"title": title, "model": "openai/gpt-test", "sandbox": True,
            "history": [{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}],
            "files": [{"path": "brief.md", "content": "# Brief"}]}
    return json.dumps({"format": lb.FORMAT, "version": 1, "exported_at": "2026-10-19T00:00:00+00:00",
                       "cohort": "2027", "workspace": workspace, "chats": [chat],
                       "preferences": prefs}).encode()


@pytest.fixture
def rig(db_target, tmp_path):
    tokens = TransferTokens(db_target, "k" * 32, ORIGIN)
    routes = import_routes(tokens, LocalImports(db_target)) + transfer_routes(
        tokens, AssetStore(db_target, LocalDirBlobs(tmp_path / "blobs")))
    return db_target, tokens, TestClient(Starlette(routes=routes), base_url=ORIGIN)


def put(client, tokens, scope, body):
    url = tokens.issue_import(scope)["upload_url"]
    assert url.startswith(ORIGIN + "/imports/")
    return client.put(url.removeprefix(ORIGIN), content=body)


def test_import_creates_inline_chats_with_files_and_preferences(rig):
    db, tokens, client = rig
    response = put(client, tokens, ALICE, bundle())
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "imported", "chats": 1, "files": 1, "preferences": "applied"}
    [chat] = AdvisorStore(db).all(ALICE)
    assert (chat.title, chat.sandbox, len(chat.history)) == ("Critic", False, 2)
    assert [f["path"] for f in SessionFiles(AdvisorStore(db)).current(ALICE, chat.id)] == ["brief.md"]
    assert lb.read_preferences(db, ALICE)["general_default"] == "openai/gpt-test"
    assert AdvisorStore(db).all(BOB) == []


def test_same_content_imports_once(rig):
    db, tokens, client = rig
    assert put(client, tokens, ALICE, bundle()).json()["status"] == "imported"
    again = put(client, tokens, ALICE, bundle())
    assert again.status_code == 200 and again.json()["status"] == "already_imported"
    assert len(AdvisorStore(db).all(ALICE)) == 1


def test_existing_choices_are_kept(rig):
    db, tokens, client = rig
    lb.seed_preferences(db, ALICE, PREFS | {"general_default": "google/gemini-test"})
    assert put(client, tokens, ALICE, bundle()).json()["preferences"] == "kept"
    assert lb.read_preferences(db, ALICE)["general_default"] == "google/gemini-test"


def test_another_cohort_is_refused(rig):
    db, tokens, client = rig
    response = put(client, tokens, ALICE, bundle(workspace=WS_2028))
    assert response.status_code == 403 and "cohort" in response.json()["error"]
    assert AdvisorStore(db).all(ALICE) == []


def test_invalid_bundle_imports_nothing(rig):
    db, tokens, client = rig
    response = put(client, tokens, ALICE, b'{"format": "nope"}')
    assert response.status_code == 400 and "not valid" in response.json()["error"]
    assert AdvisorStore(db).all(ALICE) == []


def test_link_is_single_use(rig):
    _, tokens, client = rig
    path = tokens.issue_import(ALICE)["upload_url"].removeprefix(ORIGIN)
    assert client.put(path, content=bundle()).status_code == 200
    assert client.put(path, content=bundle()).status_code == 400


def test_image_and_import_links_do_not_cross(rig):
    _, tokens, client = rig
    image = tokens.issue_upload(ALICE, "a.png", "image/png", 100)["upload_url"].removeprefix(ORIGIN)
    assert client.put(image.replace("/uploads/", "/imports/"), content=bundle()).status_code == 400
    imported = tokens.issue_import(ALICE)["upload_url"].removeprefix(ORIGIN)
    assert client.put(imported.replace("/imports/", "/uploads/"), content=b"x").status_code == 400


def test_oversized_body_is_refused(rig, monkeypatch):
    _, tokens, client = rig
    monkeypatch.setattr(lb, "MAX_BUNDLE_BYTES", 64)
    assert put(client, tokens, ALICE, bundle()).status_code == 413


async def test_tool_returns_export_and_upload_steps(db_target):
    async with running_server(db_target) as server, server.client(server.token()) as client:
        result = await client.call_tool("create_local_import", {})
        ticket = result.structured_content
        assert "/imports/" in ticket["upload_url"]
        assert "export-local" in ticket["instructions"] and "curl -T" in ticket["instructions"]
        async with httpx.AsyncClient() as http:
            response = await http.put(ticket["upload_url"], content=bundle())
    assert response.status_code == 200, response.text
    assert response.json()["chats"] == 1
