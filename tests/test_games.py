"""Game log per deck: storage, statistics, learning focus, routes and the MCP tool."""

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import games, storage
from mtgdeck.gui.app import app, upgrade_prompt, UpgradeRequest
from mtgdeck.mcp_server import mcp


async def _save(name="Games"):
    await mcp.call_tool("save_deck", {"name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    return storage.slug(name)


async def test_add_stats_and_focus():
    slug = await _save()
    games.add(slug, result="win", turn=9, mvp="Sol Ring", version=1)
    games.add(slug, result="loss", turn=7, issues=["few_lands", "no_draw"], opponents=["Atraxa", " "], version=1)
    games.add(slug, result="loss", issues=["few_lands"], version=2)
    s = games.stats(games.games(slug))
    assert (s["games"], s["wins"], s["losses"], s["win_rate"], s["avg_turn"]) == (3, 1, 2, 0.33, 8.0)
    assert s["issues"][0] == {"key": "few_lands", "label": "zu wenige Länder", "count": 2}
    assert s["per_version"] == [{"version": 1, "games": 2, "wins": 1}, {"version": 2, "games": 1, "wins": 0}]
    assert s["mvps"] == [{"name": "Sol Ring", "count": 1}] and s["opponents"] == [{"name": "Atraxa", "count": 1}]
    focus = games.learn_focus(games.games(slug))
    assert focus.startswith("Aus den Partien: konstanter Mana") and "2× „zu wenige Länder“" in focus
    with pytest.raises(ValueError):
        games.add(slug, result="gewonnen")
    with pytest.raises(ValueError):
        games.add(slug, result="win", issues=["unbekannt"])
    assert games.learn_focus([]) == ""
    # the upgrade prompt knows the record
    prompt = upgrade_prompt(storage.load(slug), UpgradeRequest(), False)
    assert "1 Siege, 2 Niederlagen" in prompt and "zu wenige Länder (2×)" in prompt


async def test_routes_and_delete_with_deck():
    slug = await _save()
    client = TestClient(app)
    r = client.post(f"/api/decks/{slug}/games", json={"result": "win", "mvp": "sol ring", "opponents": ["Meren of Clan Nel Toth"],
                                                       "issues": ["slow"], "turn": 10})  # fmt: skip
    assert r.status_code == 200
    body = r.json()
    assert body["game"]["mvp"] == "Sol Ring" and body["game"]["version"] == 1 and body["stats"]["wins"] == 1
    assert body["issue_labels"]["slow"] == "zu langsam"
    assert client.post(f"/api/decks/{slug}/games", json={"result": "maybe"}).status_code == 422
    assert client.post(f"/api/decks/{slug}/games", json={"result": "win", "issues": ["x"]}).status_code == 400
    assert client.get("/api/decks/nope/games").status_code == 404
    gid = body["game"]["id"]
    assert client.delete(f"/api/decks/{slug}/games/{gid}").json()["stats"]["games"] == 0
    assert client.delete(f"/api/decks/{slug}/games/{gid}").status_code == 404
    games.add(slug, result="draw")
    storage.delete(slug)
    assert games.games(slug) == []


async def test_mcp_deck_games():
    slug = await _save()
    games.add(slug, result="loss", issues=["combo"])
    res = await mcp.call_tool("deck_games", {"slug": slug})
    text = str(res)
    assert "gegen eine Combo verloren" in text and "learn_focus" in text
    assert "error" in str(await mcp.call_tool("deck_games", {"slug": "nope"}))
