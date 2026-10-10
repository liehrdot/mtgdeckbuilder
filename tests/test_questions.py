"""Questions about one deck go through „Frag Claude“ (the former tab „Fragen zum Deck“): the chat gets a deck focus,
and the old per-deck question logs are moved into conversations once."""

import time

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import chat, storage
from mtgdeck.gui import app as gui
from mtgdeck.jsonstore import write_json
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


def test_chat_prompt_with_a_deck_focus_loads_the_deck_first():
    deck = {"slug": "meren", "name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
            "power_profile": {"tier": "high"}, "proxy": True}  # fmt: skip
    p = gui.chat_prompt("Und gegen Kinnan?", [], deck=deck)
    assert "{{meren}}" in p and "„Meren“" in p and "oberes Bracket 3" in p and "Proxy-Deck" in p
    assert "`load_deck`" in p and "deck-questions.md" in p and "`deck_games`" in p
    assert p.index("Die Frage bezieht sich") < p.index("Übersicht der App:") and p.endswith("Frage: Und gegen Kinnan?")
    assert "Die Frage bezieht sich" not in gui.chat_prompt("Welches Deck?", [])


async def test_asking_about_a_deck_marks_the_message(monkeypatch):
    slug = await _save_deck()
    seen = {}
    answer = "**Aristocrats**: opfere mit [[Viscera Seer]], ziehe mit [[Sol Ring]]."

    async def fake_query(prompt, options):
        seen["prompt"] = prompt
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__mtg__load_deck", input={"slug": slug})], model="m")
        yield AssistantMessage(content=[TextBlock(text=answer)], model="m")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2, session_id="s", result=answer)

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with TestClient(gui.app) as client:
        r = client.post("/api/chat", json={"question": "Was ist die Strategie?", "deck": slug}).json()
        _wait(r["job"])
        assert f"{{{{{slug}}}}}" in seen["prompt"] and "`load_deck`" in seen["prompt"]
        m = client.get(f"/api/chats/{r['chat_id']}").json()["messages"][0]
        assert m["deck"] == slug and m["cards"]["Sol Ring"]["image"]
        # an unknown deck does not stop the question, it just loses the focus
        r2 = client.post("/api/chat", json={"question": "Und sonst?", "chat_id": r["chat_id"], "deck": "gibts-nicht"}).json()
        _wait(r2["job"])
        assert "Die Frage bezieht sich" not in seen["prompt"]
        assert "deck" not in client.get(f"/api/chats/{r['chat_id']}").json()["messages"][1]


async def test_old_deck_questions_become_conversations_once():
    slug = await _save_deck("Alte Fragen")
    folder = storage.DECKS_DIR / ".questions"
    write_json(folder / f"{slug}.json", [
        {"id": "q1", "asked": "2026-09-01T10:00:00+00:00", "question": "Strategie?", "answer": "Aristocrats.", "cards": {"Sol Ring": {"image": "i"}}},
        {"id": "q2", "asked": "2026-09-02T10:00:00+00:00", "question": "Gegen Atraxa?", "answer": "Eher schlecht."},
        {"id": "q3", "asked": "2026-09-03T10:00:00+00:00", "question": "Ohne Antwort?", "answer": ""},
    ])  # fmt: skip
    write_json(folder / "leer.json", [])
    assert chat.migrate_questions() == 1
    conv = [c for c in chat.chats() if c["title"].startswith("Fragen zu")]
    assert len(conv) == 1 and conv[0]["title"] == "Fragen zu „Alte Fragen“" and conv[0]["count"] == 2
    msgs = chat.get(conv[0]["id"])["messages"]
    assert [m["question"] for m in msgs] == ["Strategie?", "Gegen Atraxa?"] and msgs[0]["deck"] == slug and msgs[0]["cards"]["Sol Ring"]["image"] == "i"
    assert chat.get(conv[0]["id"])["created"] == "2026-09-01T10:00:00+00:00"
    assert not (folder / f"{slug}.json").exists() and (folder / f"{slug}.json.migriert").exists()
    assert chat.migrate_questions() == 0  # nothing left to move
    assert len([c for c in chat.chats() if c["title"].startswith("Fragen zu")]) == 1
