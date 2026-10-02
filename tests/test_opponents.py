"""Opponent decks ("Gegnerdecks"): commander + observations, linked from the game log, used in prompts."""

from __future__ import annotations

from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import games, mcp_server, opponents, storage, tablerules
from mtgdeck.gui import app as gui

ATRAXA = "Atraxa, Praetors' Voice"


def _deck(name: str = "Meren Test", **extra) -> str:
    storage.save({"name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": 3, "currency": "eur",
                  "cards": [{"name": ln.split(" ", 1)[1], "qty": int(ln.split(" ", 1)[0])} for ln in deck_lines()], **extra})  # fmt: skip
    return storage.slug(name)


async def test_create_update_notes_and_delete():
    o = await opponents.create(["atraxa, praetors' voice"], label="Tims Atraxa", player="Tim", tags=["combo", "nope", "wipes"],
                               bracket=4, note="Gewinnt mit Doubling Season")  # fmt: skip
    assert o["commanders"] == [ATRAXA] and o["tags"] == ["combo", "wipes"] and o["bracket"] == 4
    assert o["color_identity"] == ["W", "U", "B", "G"] and o["image"]
    assert opponents.title(o) == f"Tims Atraxa ({ATRAXA})"
    o = await opponents.update(o["id"], add_note="Viele Counter", tags=["counters"], bracket=None)
    assert [n["text"] for n in o["notes"]] == ["Gewinnt mit Doubling Season", "Viele Counter"]
    assert o["tags"] == ["counters"] and o["bracket"] is None
    o = await opponents.update(o["id"], remove_note=o["notes"][0]["id"])
    assert len(o["notes"]) == 1
    try:
        await opponents.create(["Gibt es nicht"])
        raise AssertionError("unknown commander accepted")
    except ValueError as exc:
        assert "nicht gefunden" in str(exc)
    assert [d["slug"] for d in storage.list_decks()] == []  # decks/.opponents.json is not a deck
    opponents.delete(o["id"])
    assert opponents.all_opponents() == []


def test_game_links_opponents_and_records():
    slug = _deck()
    with TestClient(gui.app) as client:
        r = client.post(f"/api/decks/{slug}/games", json={
            "result": "loss", "opponents": ["Atraxa, Praetors' Voice", "", "Krenko, Mob Boss"],
            "opponent_notes": ["Combo in Zug 6", "", ""], "issues": ["combo"]}).json()
        game = r["game"]
        assert game["opponents"] == [ATRAXA, "Krenko, Mob Boss"]
        assert all(game["opponent_ids"])  # both remembered as new opponent decks
        atraxa = opponents.get(game["opponent_ids"][0])
        assert atraxa["notes"][0]["text"] == "Combo in Zug 6" and atraxa["notes"][0]["game_id"] == game["id"]

        # second game: chosen by id, same deck again; no duplicate created
        client.post(f"/api/decks/{slug}/games", json={"result": "win", "opponents": [ATRAXA], "opponent_ids": [atraxa["id"]]})
        # third: typed commander matches the existing deck
        client.post(f"/api/decks/{slug}/games", json={"result": "win", "opponents": [ATRAXA]})
        # without remembering, unknown commanders stay plain names
        client.post(f"/api/decks/{slug}/games", json={"result": "draw", "opponents": ["Sol Ring"], "remember_opponents": False})
        assert client.post(f"/api/decks/{slug}/games", json={"result": "win", "issues": ["bogus"]}).status_code == 400

        data = client.get("/api/opponents").json()
        assert [o["commanders"][0] for o in data["opponents"]] == [ATRAXA, "Krenko, Mob Boss"]
        rec = data["opponents"][0]["record"]
        assert (rec["games"], rec["wins"], rec["losses"]) == (3, 2, 1)
        assert rec["per_deck"][0]["slug"] == slug and rec["history"][0]["result"] == "win"
        assert data["tags"]["combo"] == "Combo"
        assert len(games.games(slug)) == 4


async def test_prompt_lines_pick_relevant_opponents():
    slug = _deck()
    other = _deck("Other Deck")
    t = await tablerules.create("Freitag")
    a = await opponents.create([ATRAXA], label="Tims Atraxa", tags=["combo"], note="Thassa's Oracle")
    k = await opponents.create(["Krenko, Mob Boss"], table_rule=t["id"])
    games.add(slug, result="loss", opponents=[ATRAXA], opponent_ids=[a["id"]])

    deck = storage.load(slug)
    text = "\n".join(opponents.prompt_lines(deck))
    assert "Tims Atraxa" in text and "deine Bilanz 0–1 (mit diesem Deck 0–1)" in text
    assert "Combo → Interaktion gegen Combos" in text and "„Thassa's Oracle“" in text
    assert "Krenko" not in text  # neither faced with this deck nor at its table

    deck["table_rule"] = t["id"]
    assert "Krenko" in "\n".join(opponents.prompt_lines(deck))
    # a deck without any link falls back to the opponents actually played
    assert "Tims Atraxa" in "\n".join(opponents.prompt_lines(storage.load(other)))
    focus = "\n".join(opponents.prompt_lines(storage.load(other), focus_id=k["id"]))
    assert "Schwerpunkt: das Deck soll besser gegen Krenko, Mob Boss werden" in focus

    up = gui.upgrade_prompt(deck, gui.UpgradeRequest(opponent_id=a["id"]), False)
    assert "Gegnerdecks des Nutzers" in up and "besser gegen Tims Atraxa" in up
    assert "Gegnerdecks des Nutzers" in gui.refine_prompt(gui.RefineRequest(slug=slug, request="x"), deck)
    build = gui.build_prompt(gui.BuildRequest(commander="Meren of Clan Nel Toth", table_rule=t["id"]))
    assert "Krenko" in build
    assert "Gegnerdecks" not in gui.build_prompt(gui.BuildRequest(commander="Meren of Clan Nel Toth"))


def test_gui_routes():
    with TestClient(gui.app) as client:
        r = client.post("/api/opponents", json={"commanders": ["Krenko, Mob Boss"], "player": "Ana", "tags": ["tokens"], "note": "Sehr schnell"})
        assert r.status_code == 200
        oid = r.json()["id"]
        assert r.json()["tag_labels"] == ["Token-Schwärme"]
        assert client.post("/api/opponents", json={"commanders": []}).status_code == 400
        r = client.patch(f"/api/opponents/{oid}", json={"label": "Anas Goblins", "note": "Krenko + Skirk Prospector"}).json()
        assert r["title"] == "Anas Goblins (Krenko, Mob Boss)" and len(r["notes"]) == 2 and r["player"] == "Ana"
        assert client.get(f"/api/opponents/{oid}").json()["label"] == "Anas Goblins"
        assert client.delete(f"/api/opponents/{oid}").json() == {"deleted": oid}
        assert client.get(f"/api/opponents/{oid}").status_code == 404


async def test_mcp_tools():
    out = await mcp_server.update_opponent_deck(commander="Atraxa, Praetors' Voice", note="Proliferate-Kontrolle", tags=["counters"])
    assert out["traits"] == ["viele Counterspells"] and out["notes"] == ["Proliferate-Kontrolle"]
    out = await mcp_server.update_opponent_deck(commander="Atraxa, Praetors' Voice", note="Nimmt Removal von mir")
    assert len(out["notes"]) == 2
    listed = await mcp_server.opponent_decks()
    assert listed["count"] == 1 and listed["opponent_decks"][0]["traits"] == ["viele Counterspells"]
    await opponents.create([ATRAXA], label="Zweite Atraxa")
    amb = await mcp_server.update_opponent_deck(commander=ATRAXA, note="x")
    assert "Mehrere" in amb["error"] and len(amb["candidates"]) == 2
    assert (await mcp_server.update_opponent_deck(commander=ATRAXA, opponent_id=amb["candidates"][1]["id"], delete=True))["deleted"]


async def test_meta_build_prompt_and_route(monkeypatch):
    slug = _deck("Meren Aristo")
    games.add(slug, result="loss", opponents=[ATRAXA])
    a = await opponents.create([ATRAXA], label="Tims Atraxa", tags=["combo"], note="Thassa's Oracle")
    k = await opponents.create(["Krenko, Mob Boss"])
    t = await tablerules.create("Freitag", max_bracket=3)

    req = gui.MetaBuildRequest(opponent_ids=[a["id"]], bracket=3, table_rule=t["id"], proxy=True)
    text = gui.meta_prompt(req)
    assert "Abschnitt „Gegen die Runde bauen“" in text and "`commander-finder`" in text
    assert "Tims Atraxa" in text and "Krenko" not in text  # only the chosen opponents
    assert "„Meren Aristo“ (Meren of Clan Nel Toth, Bracket 3" in text and f"verloren gegen {ATRAXA}" in text
    assert "Commander: frei wählbar" in text and "Tischregel „Freitag“" in text and "proxy=true" in text
    assert "\n" in text and "\\n" not in text
    fixed = gui.meta_prompt(gui.MetaBuildRequest(commander="Meren of Clan Nel Toth"))
    assert "- Commander: Meren of Clan Nel Toth" in fixed and "Krenko" in fixed and "`commander-finder`" not in fixed

    started = {}

    def fake_start(prompt, model, output_format=None, **kw):
        started.update(prompt=prompt, **kw)
        return {"job": "x"}

    monkeypatch.setattr(gui, "_start", fake_start)
    with TestClient(gui.app) as client:
        assert client.post("/api/build-meta", json={"opponent_ids": ["nope", k["id"]], "table_rule": t["id"]}).json() == {"job": "x"}
        assert started["extras"] == {"built_against": [k["id"]]} and started["table_rule"] == t["id"]
        assert client.post("/api/build-meta", json={"table_rule": "gone0000"}).status_code == 404
    # the built deck later prefers the opponents it was built against
    deck = storage.load(slug)
    deck["built_against"] = [k["id"]]
    assert [o["id"] for o in opponents.relevant(deck)] == [k["id"], a["id"]]  # built against (+4) before one game (+3)


async def test_deck_shows_built_against():
    slug = _deck("Meta Deck")
    k = await opponents.create(["Krenko, Mob Boss"], label="Anas Goblins")
    storage.set_extra(slug, "built_against", [k["id"], "gone0000"])
    with TestClient(gui.app) as client:
        deck = client.get(f"/api/decks/{slug}").json()
    assert deck["built_against_info"] == [{"id": k["id"], "title": "Anas Goblins (Krenko, Mob Boss)"}]


async def test_meta_suggest_runs_opus_xhigh_with_optional_research(monkeypatch):
    a = await opponents.create([ATRAXA], label="Tims Atraxa", tags=["combo"])
    started = {}

    def fake_start(prompt, model, output_format=None, **kw):
        started.update(prompt=prompt, model=model, output_format=output_format, **kw)
        return {"job": "x"}

    monkeypatch.setattr(gui, "_start", fake_start)
    with TestClient(gui.app) as client:
        assert client.get("/api/build-meta/suggestions").json() == {}
        r = client.post("/api/build-meta/suggest", json={"count": 3, "bracket": 3, "model": "haiku"})
        assert r.json() == {"job": "x"}
        assert client.post("/api/build-meta/suggest", json={"count": 9}).status_code == 422
    assert started["model"] == "claude-opus-5-5" and started["effort"] == "xhigh"  # fixed, the form's model is ignored
    assert started["web"] is True and started["read_only"] is True
    assert started["output_format"]["schema"] is gui.META_SUGGEST_SCHEMA
    p = started["prompt"]
    assert "Schlage 3 Commander-Decks vor" in p and "KEIN Deck" in p and "Tims Atraxa" in p and "`WebSearch`" in p

    job = gui.Job(id="t")
    structured = {"analysis": "Die Runde ist combo-lastig.", "sources": ["https://edhrec.com"],
                  "suggestions": [{"name": "Krenko, Mob Boss", "archetype": "Goblins", "why": "Schnell.", "win_plan": "Tokens.",
                                   "strategy": "Goblin-Tokens", "matchups": [{"opponent": "Tims Atraxa", "plan": "Druck machen."}]}]}  # fmt: skip
    await started["finish"](job, True, "", structured)
    ev = next(e for e in job.events if e["type"] == "meta_suggestions")["result"]
    assert ev["analysis"].startswith("Die Runde") and ev["suggestions"][0]["image"] and ev["model"] == "claude-opus-5-5"
    assert job.events[-1] == {"type": "done", "ok": True, "deck": None}
    with TestClient(gui.app) as client:
        last = client.get("/api/build-meta/suggestions").json()
    assert last["suggestions"][0]["name"] == "Krenko, Mob Boss" and last["request"]["count"] == 3
    assert [d["slug"] for d in storage.list_decks()] == []  # the dot file is no deck

    gui.meta_suggest_prompt(gui.MetaSuggestRequest(research=False, opponent_ids=[a["id"]]))
    with TestClient(gui.app) as client:
        client.post("/api/build-meta/suggest", json={"research": False})
    assert started["web"] is False and "`WebSearch`" not in started["prompt"]


def test_run_claude_passes_effort_and_web_tools(monkeypatch):
    import claude_agent_sdk

    seen = {}

    async def fake_query(prompt, options):
        seen["options"] = options
        if False:
            yield None

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    job = gui.Job(id="r")

    async def finish(*_):
        pass

    import asyncio

    asyncio.run(gui._run_claude(job, "x", "claude-opus-5-5", read_only=True, finish=finish, effort="xhigh", web=True))
    o = seen["options"]
    assert o.model == "claude-opus-5-5" and o.effort == "xhigh"
    assert "WebSearch" in o.allowed_tools and "WebFetch" in o.allowed_tools
    asyncio.run(gui._run_claude(job, "x", None, finish=finish))
    assert seen["options"].effort is None and "WebSearch" not in seen["options"].allowed_tools
