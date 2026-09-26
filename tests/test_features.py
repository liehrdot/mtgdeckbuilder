"""Blacklist, proxy/budget handling and the commander finder."""

import json

from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import blacklist
from mtgdeck.gui import app as gui
from mtgdeck.mcp_server import mcp
from mtgdeck.validate import validate_deck

MEREN = ["Meren of Clan Nel Toth"]


def _payload(result):
    return json.loads(result.content[0].text)


async def test_blacklist_add_remove_and_file_format():
    r = await blacklist.update(add=["sol ring", "Cultivate", "Nonexistent Card"])
    assert r["added"] == ["Sol Ring", "Cultivate"]  # resolved to Oracle names
    assert r["not_found"] == ["Nonexistent Card"]
    assert blacklist.load() == ["Cultivate", "Sol Ring"]
    text = blacklist.BLACKLIST_FILE.read_text()
    assert text.startswith("#") and "Sol Ring\n" in text

    r = await blacklist.update(remove=["SOL RING"])
    assert r["removed"] == ["SOL RING"]
    assert blacklist.load() == ["Cultivate"]


async def test_blacklisted_card_is_a_validation_error():
    await blacklist.update(add=["Sol Ring"])
    r = await validate_deck(MEREN, deck_lines(), 2)
    assert not r["legal"]
    assert any("Blacklist" in e and "Sol Ring" in e for e in r["errors"])


async def test_blacklist_filters_search_results():
    await blacklist.update(add=["Sol Ring"])
    r = _payload(await mcp.call_tool("edhrec_recommendations", {"commander": "Meren of Clan Nel Toth"}))
    assert r["categories"][0]["cards"] == []
    assert _payload(await mcp.call_tool("get_blacklist", {}))["blacklist"] == ["Sol Ring"]


async def test_budget_warning_and_proxy():
    extra = ["1 Demonic Tutor"]  # everything costs 0.50 in the mock -> ~31 EUR total
    over = await validate_deck(MEREN, deck_lines(extra), 4, budget=10)
    assert any("Budget überschritten" in w for w in over["warnings"])
    assert over["price_total"] > 10

    proxy = await validate_deck(MEREN, deck_lines(extra), 4, budget=10, proxy=True)
    assert not any("Budget" in w for w in proxy["warnings"])
    assert proxy["proxy"] is True and proxy["budget"] is None


async def test_save_deck_stores_proxy_flag():
    cards = [{"name": line.split(" ", 1)[1], "qty": int(line.split(" ", 1)[0])} for line in deck_lines()]
    r = _payload(await mcp.call_tool("save_deck", {
        "name": "Proxy Deck", "commanders": MEREN, "cards": cards, "bracket": 2, "budget": 5, "proxy": True,
    }))  # fmt: skip
    assert r["proxy"] is True and r["legal"]
    deck = _payload(await mcp.call_tool("load_deck", {"slug": "proxy-deck"}))
    assert deck["proxy"] is True and deck["budget"] is None


def test_prompts_mention_proxy_and_blacklist():
    p = gui.build_prompt(gui.BuildRequest(commander="Meren of Clan Nel Toth", budget=50, proxy=True))
    assert "geproxt" in p and "proxy=true" in p and "max. 50" not in p and "Blacklist" in p
    p = gui.build_prompt(gui.BuildRequest(commander="Meren of Clan Nel Toth", budget=50))
    assert "max. 50 EUR" in p
    f = gui.finder_prompt(gui.FinderRequest(prompt="Drachen, viel Ramp", bracket=3, proxy=True))
    assert "commander-finder" in f and "Drachen, viel Ramp" in f and "Bracket: 3" in f and "KEIN Deck" in f


async def test_enrich_suggestions_adds_card_data():
    items = await gui._enrich_suggestions(
        {"suggestions": [{"name": "Meren of Clan Nel Toth", "archetype": "Aristocrats", "why": "Passt."}]}
    )
    assert items[0]["image"].split("?")[0].endswith("Meren of Clan Nel Toth.jpg")
    assert items[0]["color_identity"] == ["B", "G"]
    assert await gui._enrich_suggestions(None) == []


def test_gui_blacklist_routes_and_finder_start(monkeypatch):
    started = {}

    def fake_start(prompt, model, output_format=None):
        started.update(prompt=prompt, output_format=output_format)
        return {"job": "x"}

    monkeypatch.setattr(gui, "_start", fake_start)
    client = TestClient(gui.app)
    assert client.post("/api/blacklist", json={"add": ["Sol Ring"]}).json()["added"] == ["Sol Ring"]
    assert client.get("/api/blacklist").json() == ["Sol Ring"]
    client.post("/api/blacklist", json={"remove": ["Sol Ring"]})
    assert client.get("/api/blacklist").json() == []

    assert client.post("/api/find-commander", json={"prompt": "Vampire", "bracket": 2}).json() == {"job": "x"}
    assert started["output_format"]["type"] == "json_schema"
    assert "suggestions" in started["output_format"]["schema"]["properties"]
