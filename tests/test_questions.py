"""'Fragen zum Deck': read-only Claude job, Q&A log per deck, card references in answers."""

import time

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import storage
from mtgdeck.gui import app as gui
from mtgdeck.mcp_server import mcp


async def _save_deck(name="Qa Deck"):
    await mcp.call_tool("save_deck", {
        "name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
        "cards": [{"name": line.split(" ", 1)[1], "qty": int(line.split(" ", 1)[0])} for line in deck_lines()],
    })  # fmt: skip
    return storage.slug(name)


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if gui.JOBS[job_id].done:
            return gui.JOBS[job_id]
        time.sleep(0.05)
    raise AssertionError(gui.JOBS[job_id].events)


async def test_question_log_roundtrip():
    slug = await _save_deck()
    assert storage.questions(slug) == []
    a = storage.add_question(slug, "Strategie?", "Aristocrats.", version=1)
    b = storage.add_question(slug, "Gegen Atraxa?", "Eher schlecht.", version=1)
    assert [q["question"] for q in storage.questions(slug)] == ["Strategie?", "Gegen Atraxa?"]
    assert storage.delete_questions(slug, a["id"]) == 1
    assert [q["id"] for q in storage.questions(slug)] == [b["id"]]
    storage.delete(slug)  # deleting the deck removes its questions too
    assert storage.questions(slug) == []


def test_ask_prompt_is_read_only_and_carries_history():
    deck = {"slug": "meren", "name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
            "power_profile": {"tier": "high"}, "proxy": True}  # fmt: skip
    history = [{"question": f"Frage {i}", "answer": "x" * 2000 if i == 5 else f"Antwort {i}"} for i in range(6)]
    p = gui.ask_prompt("  Und gegen Kinnan? ", deck, history)
    assert "`meren`" in p and "oberes Bracket 3" in p and "Proxy-Deck" in p
    assert "Nur lesen" in p and "[[Kartenname]]" in p and "deck-questions.md" in p
    assert "Frage 1" not in p and "Frage 2" in p and "Frage 5" in p  # only the latest ASK_HISTORY
    assert "x" * 1200 + " …" in p and "x" * 1201 not in p
    assert p.endswith("Frage: Und gegen Kinnan?")


async def test_ask_route_runs_read_only_job_and_stores_answer(monkeypatch):
    slug = await _save_deck()
    seen = {}
    answer = "**Aristocrats**: opfere mit [[Viscera Seer]], ziehe mit [[Sol Ring]] … und [[Nonexistent Card]]."

    async def fake_query(prompt, options):
        seen.update(prompt=prompt, options=options)
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__mtg__load_deck", input={"slug": slug})], model="m")
        yield AssistantMessage(content=[TextBlock(text=answer)], model="m")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
                            session_id="s", result=answer)  # fmt: skip

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    client = TestClient(gui.app)
    with client:
        job = client.post(f"/api/decks/{slug}/ask", json={"question": "Was ist die Strategie?"}).json()["job"]
        j = _wait(job)
    opts = seen["options"]
    assert "mcp__mtg__load_deck" in opts.allowed_tools and "mcp__mtg" not in opts.allowed_tools
    assert "mcp__mtg__save_deck" in opts.disallowed_tools and "Bash" in opts.disallowed_tools
    assert "Frage: Was ist die Strategie?" in seen["prompt"]

    types = [e["type"] for e in j.events]
    assert "tool" in types and j.events[-1] == {"type": "done", "ok": True, "deck": None}
    entry = next(e["entry"] for e in j.events if e["type"] == "answer")
    assert entry["answer"] == answer and entry["version"] == 1
    assert entry["cards"]["Sol Ring"]["image"] and "Nonexistent Card" not in entry["cards"]

    listed = client.get(f"/api/decks/{slug}/questions").json()
    assert [q["id"] for q in listed] == [entry["id"]]
    assert storage.load(slug)["version"] == 1  # the deck itself is untouched

    # follow-up questions carry the earlier ones
    with client:
        job = client.post(f"/api/decks/{slug}/ask", json={"question": "Und gegen Atraxa?"}).json()["job"]
        _wait(job)
    assert "Frage: Was ist die Strategie?" in seen["prompt"] and seen["prompt"].endswith("Frage: Und gegen Atraxa?")

    assert client.delete(f"/api/decks/{slug}/questions").json() == {"deleted": 2}
    assert client.post("/api/decks/nope/ask", json={"question": "Hallo?"}).status_code == 404


async def test_failed_run_stores_nothing(monkeypatch):
    slug = await _save_deck()

    async def failing_query(prompt, options):
        yield ResultMessage(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=9,
                            session_id="s")  # fmt: skip

    monkeypatch.setattr(claude_agent_sdk, "query", failing_query)
    client = TestClient(gui.app)
    with client:
        j = _wait(client.post(f"/api/decks/{slug}/ask", json={"question": "Strategie?"}).json()["job"])
    assert j.events[-1] == {"type": "done", "ok": False, "deck": None}
    assert storage.questions(slug) == []
