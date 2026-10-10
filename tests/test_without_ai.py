"""The app without Claude: AI features say so up front, everything else keeps working."""

import time

import claude_agent_sdk
from claude_agent_sdk import ResultMessage
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import collection, settings, storage
from mtgdeck.gui import app as gui
from mtgdeck.mcp_server import mcp


async def _deck():
    await mcp.call_tool("save_deck", {"name": "Ohne KI", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    return "ohne-ki"


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if gui.JOBS[job_id].done:
            return gui.JOBS[job_id]
        time.sleep(0.05)
    raise AssertionError(gui.JOBS[job_id].events)


def test_ai_switched_off_blocks_ai_routes_with_a_clear_message():
    settings.update({"ai_enabled": False})
    client = TestClient(gui.app)
    st = client.get("/api/ai").json()
    assert st == {"enabled": False, "available": False, "reason": "KI-Funktionen sind in den Einstellungen ausgeschaltet."}
    r = client.post("/api/build", json={"commander": "Meren of Clan Nel Toth"})
    assert r.status_code == 503 and "funktioniert weiter" in r.json()["detail"]
    assert client.post("/api/chat", json={"question": "Was ist mein stärkstes Deck?"}).status_code == 503
    assert client.get("/api/chats").json() == []  # no empty conversation left behind
    assert client.post("/api/find-commander", json={"prompt": "Zombies"}).status_code == 503


def test_missing_claude_cli_is_reported(monkeypatch):
    monkeypatch.setattr(gui, "_find_claude_cli", lambda: None)
    st = TestClient(gui.app).get("/api/ai").json()
    assert st["enabled"] and not st["available"] and "Claude Code wurde nicht gefunden" in st["reason"]


def test_failed_login_is_remembered_until_a_run_succeeds(monkeypatch):
    class CLINotFoundError(Exception):
        pass

    async def broken(prompt, options):
        raise CLINotFoundError("Claude Code not found")
        yield  # pragma: no cover

    async def fine(prompt, options):
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="s", result="ok")  # fmt: skip

    gui._ai_failure.clear()
    monkeypatch.setattr(claude_agent_sdk, "query", broken)
    with TestClient(gui.app) as client:
        j = _wait(client.post("/api/find-commander", json={"prompt": "Zombies"}).json()["job"])
        err = next(e["text"] for e in j.events if e["type"] == "error")
        assert "Claude Code ist nicht bereit" in err and "drucken" in err
        assert "Claude Code not found" in client.get("/api/ai").json()["last_error"]
        monkeypatch.setattr(claude_agent_sdk, "query", fine)
        _wait(client.post("/api/find-commander", json={"prompt": "Zombies"}).json()["job"])
        assert "last_error" not in client.get("/api/ai").json()


async def test_without_ai_decks_print_and_collection_work():
    settings.update({"ai_enabled": False})
    with TestClient(gui.app) as client:
        # an empty deck by hand, then filled via the editor
        r = client.post("/api/decks/new", json={"commander": "Meren of Clan Nel Toth", "bracket": 2})
        assert r.status_code == 200, r.text
        slug = r.json()["slug"]
        deck = storage.load(slug)
        assert deck["commanders"] == ["Meren of Clan Nel Toth"] and deck["cards"] == [] and deck["name"] == "Meren of Clan Nel Toth"
        assert deck["history"][-1]["note"] == "Leer angelegt"
        r = client.post(f"/api/decks/{slug}/cards", json={"add": [{"name": "Sol Ring", "qty": 1}]})
        assert r.status_code == 200 and [c["name"] for c in storage.load(slug)["cards"]] == ["Sol Ring"]
        assert client.post("/api/decks/new", json={"commander": "Gibts Nicht Karte"}).status_code == 404

        # printing and the collection need no AI
        full = await _deck()
        plan = client.get(f"/api/decks/{full}/print/plan").json()
        assert plan["quantity"] == 100
        assert client.post("/api/collection", json={"items": [{"name": "Sol Ring", "qty": 2}]}).status_code == 200
        assert collection.summary()["cards"] == 2

