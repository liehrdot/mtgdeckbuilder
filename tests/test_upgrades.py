"""Upgrade suggestions: structured, read-only Claude run; invalid swaps are dropped."""

import time

import claude_agent_sdk
from claude_agent_sdk import ResultMessage
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import collection, storage
from mtgdeck.gui import app as gui
from mtgdeck.mcp_server import mcp


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if gui.JOBS[job_id].done:
            return gui.JOBS[job_id]
        time.sleep(0.05)
    raise AssertionError(gui.JOBS[job_id].events)


async def test_upgrade_suggestions(monkeypatch):
    await mcp.call_tool("save_deck", {"name": "Up", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3, "budget": 100,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    await collection.add([{"name": "Demonic Tutor"}])
    seen = {}
    structured = {"summary": "Mehr Tutoren", "upgrades": [
        {"add": "Demonic Tutor", "remove": "Filler 1", "reason": "Findet Meren", "impact": "Konstanz", "price": 30},
        {"add": "Vampiric Tutor", "remove": "Filler 2", "reason": "Schnell"},
        {"add": "Sol Ring", "remove": "Filler 3", "reason": "schon im Deck"},        # add already in deck
        {"add": "Vampiric Tutor", "remove": "Filler 4", "reason": "doppelt"},        # duplicate add
        {"add": "Necropotence", "remove": "Not In Deck", "reason": "falsches raus"},  # remove not in deck
        {"add": "Gibt Es Nicht", "remove": "Filler 5", "reason": "unbekannt"},
    ]}  # fmt: skip

    async def fake_query(prompt, options):
        seen.update(prompt=prompt, options=options)
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
                            session_id="s", structured_output=structured)  # fmt: skip

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    client = TestClient(gui.app)
    with client:
        job = client.post("/api/decks/up/upgrades", json={"budget": 40, "focus": "Tutoren", "count": 5}).json()["job"]
        j = _wait(job)
    ev = next(e for e in j.events if e["type"] == "upgrades")
    assert [(i["add"], i["remove"], i["owned"]) for i in ev["items"]] == [("Demonic Tutor", "Filler 1", True), ("Vampiric Tutor", "Filler 2", False)]
    assert ev["items"][0]["price"] == 0.5 and ev["summary"] == "Mehr Tutoren"  # current price, not Claude's guess
    assert j.events[-1] == {"type": "done", "ok": True, "deck": None}
    p = seen["prompt"]
    assert "max. 40 EUR" in p and "Fokus des Nutzers: Tutoren" in p and "Bis zu 5 Tausche" in p and "collection_search" in p
    opts = seen["options"]
    assert opts.output_format["schema"]["required"] == ["upgrades"] and "mcp__mtg__save_deck" in opts.disallowed_tools
    assert "mcp__mtg__similar_cards" in opts.allowed_tools
    assert storage.load("up")["version"] == 1  # nothing saved
