"""Exports for Cockatrice and Tabletop Simulator."""

import json
import xml.etree.ElementTree as ET

from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


async def _deck():
    await mcp.call_tool("save_deck", {"name": "Export Me", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2, "description": "Test & mehr",
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines(extra=["1 Delver of Secrets // Insectile Aberration"], fillers=60)]})  # fmt: skip


async def test_cockatrice_export():
    await _deck()
    r = TestClient(app).get("/api/decks/export-me/export/cockatrice")
    assert r.status_code == 200 and 'filename="export-me.cod"' in r.headers["content-disposition"]
    root = ET.fromstring(r.text)
    assert root.find("deckname").text == "Export Me" and root.find("comments").text == "Test & mehr"
    main = {c.get("name"): int(c.get("number")) for c in root.find("zone[@name='main']")}
    assert main["Forest"] == 18 and main["Delver of Secrets"] == 1  # front face of a transform card
    assert [c.get("name") for c in root.find("zone[@name='side']")] == ["Meren of Clan Nel Toth"]
    assert sum(main.values()) == 99


async def test_tts_export_and_text():
    await _deck()
    client = TestClient(app)
    data = json.loads(client.get("/api/decks/export-me/export/tts").text)
    deck, commander = data["ObjectStates"]
    assert deck["Name"] == "DeckCustom" and len(deck["ContainedObjects"]) == 99 == len(deck["DeckIDs"])
    forest = next(o for o in deck["ContainedObjects"] if o["Nickname"] == "Forest")
    face = next(iter(forest["CustomDeck"].values()))
    assert face["FaceURL"].startswith("https://cards.scryfall.io/large/") and face["BackURL"].startswith("https://backs.scryfall.io/")
    assert commander["Nickname"] == "Meren of Clan Nel Toth" and commander["Transform"]["rotZ"] == 0.0
    assert client.get("/api/decks/export-me/export/text").text.startswith("1 Meren of Clan Nel Toth *CMDR*")
    assert client.get("/api/decks/export-me/export/nope").status_code == 404
