"""„Frag Claude“: a read-only chat with the whole app – overview, conversations, route."""

import json
import time

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import chat, games, overview, storage
from mtgdeck.gui import app as gui
from mtgdeck.mcp_server import mcp


async def _save_deck(name, bracket=3, extra=None):
    await mcp.call_tool("save_deck", {
        "name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": bracket,
        "cards": [{"name": line.split(" ", 1)[1], "qty": int(line.split(" ", 1)[0])} for line in deck_lines(extra)],
    })  # fmt: skip
    return storage.slug(name)


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if gui.JOBS[job_id].done:
            return gui.JOBS[job_id]
        time.sleep(0.05)
    raise AssertionError(gui.JOBS[job_id].events)


async def test_overview_has_power_health_record_and_prompt_lines():
    a = await _save_deck("Meren Stark", bracket=3)
    b = await _save_deck("Meren Sanft", bracket=2)
    games.add(a, result="win", opponents=["Atraxa, Praetors' Voice"], turn=8)
    games.add(a, result="loss", opponents=["Krenko, Mob Boss"], issues=["few_lands"])

    data = overview.build()
    rows = {d["slug"]: d for d in data["decks"]}
    assert set(rows) == {a, b}
    row = rows[a]
    assert row["level"] == "Bracket 3" and row["legal"] is True and row["power"] is not None
    assert row["games"]["games"] == 2 and row["games"]["wins"] == 1 and row["games"]["lost_to"] == ["Krenko, Mob Boss"]
    assert row["health"]["status"] in ("green", "yellow", "red") and isinstance(row["missing_cards"], int)
    assert rows[b]["games"]["games"] == 0

    lines = "\n".join(overview.prompt_lines(data))
    assert f"{{{{{a}}}}} „Meren Stark“" in lines and "Bilanz 1–1 in 2 Partien" in lines and "verloren gegen Krenko" in lines
    assert f"{{{{{b}}}}}" in lines and "noch keine Partien" in lines
    assert "Power " in lines and "," in lines.split("Power ")[1][:4]  # German decimal comma


async def test_mcp_app_overview_tool():
    slug = await _save_deck("Meren Tool")
    result = await mcp.call_tool("app_overview", {})
    data = result.structured_content if hasattr(result, "structured_content") else json.loads(result[0].text)
    assert [d["slug"] for d in data["decks"]] == [slug]
    assert "app_overview" in gui.READ_ONLY_TOOLS


def test_conversation_storage_roundtrip():
    c = chat.create("Was ist mein stärkstes Deck und warum ist das eigentlich so, erklär es mir bitte ganz genau?")
    assert c["title"].endswith("…") and len(c["title"]) <= chat.TITLE_LEN
    chat.add(c["id"], "Frage 1", "Antwort 1")
    time.sleep(1.1)
    d = chat.create("Zweites Gespräch")
    chat.add(d["id"], "Frage", "Antwort")
    assert [x["id"] for x in chat.chats()] == [d["id"], c["id"]]
    assert chat.chats()[1]["count"] == 1
    assert chat.rename(c["id"], "Stärke")["title"] == "Stärke"
    chat.delete(d["id"])
    assert [x["id"] for x in chat.chats()] == [c["id"]]
    for bad in ("../x", "nope"):
        try:
            chat.get(bad)
        except FileNotFoundError:
            continue
        raise AssertionError(bad)


async def test_chat_route_answers_with_deck_and_card_links(monkeypatch):
    slug = await _save_deck("Meren Aristocrats")
    seen = {}
    answer = f"**{{{{{slug}}}}}** ist dein stärkstes Deck – [[Sol Ring]] und Meren tragen es. {{{{gibts-nicht}}}}"

    async def fake_query(prompt, options):
        seen.setdefault("prompts", []).append(prompt)
        seen["options"] = options
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__mtg__app_overview", input={})], model="m")
        yield AssistantMessage(content=[TextBlock(text=answer)], model="m")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
                            session_id="s", result=answer)  # fmt: skip

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with TestClient(gui.app) as client:
        r = client.post("/api/chat", json={"question": "Was ist mein stärkstes Deck?"}).json()
        j = _wait(r["job"])
        chat_id = r["chat_id"]
        assert r["title"] == "Was ist mein stärkstes Deck?"
        entry = next(e for e in j.events if e["type"] == "answer")["entry"]
        assert j.events[-1]["ok"] is True
        assert entry["decks"] == {slug: {"name": "Meren Aristocrats", "commanders": ["Meren of Clan Nel Toth"]}}
        assert entry["cards"]["Sol Ring"]["image"]

        # read-only and the overview is in the prompt
        opts = seen["options"]
        assert "mcp__mtg__app_overview" in opts.allowed_tools and "mcp__mtg__save_deck" in opts.disallowed_tools
        assert f"{{{{{slug}}}}} „Meren Aristocrats“" in seen["prompts"][0] and "references/app-chat.md" in seen["prompts"][0]

        # follow-up in the same conversation, „gründlich“ = Opus 5.5 at extra-high effort
        r2 = client.post("/api/chat", json={"question": "Und das zweitbeste?", "chat_id": chat_id, "deep": True}).json()
        _wait(r2["job"])
        assert r2["chat_id"] == chat_id
        assert seen["options"].model == gui.META_MODEL and seen["options"].effort == gui.META_EFFORT
        assert "Frage: Was ist mein stärkstes Deck?" in seen["prompts"][1] and seen["prompts"][1].endswith("Frage: Und das zweitbeste?")

        conv = client.get(f"/api/chats/{chat_id}").json()
        assert [m["question"] for m in conv["messages"]] == ["Was ist mein stärkstes Deck?", "Und das zweitbeste?"]
        assert conv["messages"][1]["deep"] is True
        assert client.get("/api/chats").json()[0]["count"] == 2
        assert client.put(f"/api/chats/{chat_id}", json={"title": "Stärke"}).json()["title"] == "Stärke"
        assert client.post("/api/chat", json={"question": "Hallo?", "chat_id": "000000000000"}).status_code == 404
        assert client.delete(f"/api/chats/{chat_id}").json() == {"ok": True}
        assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_failed_first_question_keeps_no_empty_conversation(monkeypatch):
    async def failing_query(prompt, options):
        yield ResultMessage(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=9,
                            session_id="s")  # fmt: skip

    monkeypatch.setattr(claude_agent_sdk, "query", failing_query)
    with TestClient(gui.app) as client:
        r = client.post("/api/chat", json={"question": "Was ist los?"}).json()
        j = _wait(r["job"])
        assert j.events[-1] == {"type": "done", "ok": False, "deck": None}
        assert client.get("/api/chats").json() == []
