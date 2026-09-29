"""Deck import via link (several sites) or pasted list: preview, commander choice, saving."""

import pytest
from fastapi.testclient import TestClient

from mtgdeck import deckimport, importers, storage
from mtgdeck.gui.app import app


async def test_archidekt_categories_and_excluded_boards():
    d = await importers.import_url("https://archidekt.com/decks/4242/meren")
    names = {line.split(" ", 1)[1] for line in d["cards"]}
    assert d["commanders"] == ["Meren of Clan Nel Toth"] and d["name"] == "Meren Archidekt"
    assert "Necropotence" not in names and "Demonic Tutor" not in names  # not in deck / maybeboard
    assert d["categories"]["Sol Ring"] == "Ramp" and d["categories"]["Cultivate"] == "Ramp"
    assert d["categories"]["Forest"] == "Land" and "Pitiless Plunderer" not in d["categories"]
    assert importers.map_category("Board Wipes") == "Board Wipe" and importers.map_category("Beaters") == ""


async def test_moxfield_falls_back_to_v2_and_explains_block():
    d = await importers.import_url("https://www.moxfield.com/decks/AbC123")
    assert d["commanders"] == ["Meren of Clan Nel Toth"] and "37 Forest" in d["cards"] and d["site"] == "Moxfield"
    with pytest.raises(RuntimeError, match="Liste einfügen"):
        await importers.import_url("https://moxfield.com/decks/blocked")


async def test_text_sites():
    g = await importers.import_url("https://www.mtggoldfish.com/deck/777#paper")
    assert g["commanders"] == [] and g["commander_hint"] == "Meren of Clan Nel Toth"
    t = await importers.import_url("https://tappedout.net/mtg-decks/meren-grave-fun/")
    assert t["commanders"] == ["Meren of Clan Nel Toth"] and t["name"] == "Meren Grave Fun"
    s = await importers.import_url("https://deckstats.net/decks/12/345-meren-deck/de")
    assert s["commanders"] == ["Meren of Clan Nel Toth"] and "1 Sol Ring" in s["cards"] and s["name"] == "Meren Deck"
    e = await importers.import_url("https://edhrec.com/commanders/meren-of-clan-nel-toth")
    assert e["commander_hint"] == "meren-of-clan-nel-toth" and e["name"].startswith("EDHREC-Durchschnitt")
    with pytest.raises(ValueError, match="Unterstützt"):
        await importers.import_url("https://example.com/deck/1")


def test_pasted_text():
    d = importers.import_text("Commander\n1 Meren of Clan Nel Toth\n\nDeck\n1 Sol Ring (C21) 263\n2 Forest")
    assert d["commanders"] == ["Meren of Clan Nel Toth"] and d["cards"] == ["1 Sol Ring", "2 Forest"]
    with pytest.raises(ValueError):
        importers.import_text("  ")


async def test_preview_suggests_commander_from_hint():
    for url in ("https://www.mtggoldfish.com/deck/777", "https://edhrec.com/average-decks/meren-of-clan-nel-toth"):
        p = await deckimport.preview(await importers.import_url(url))
        assert p["suggested"] == ["Meren of Clan Nel Toth"], url
        assert "Meren of Clan Nel Toth" in {c["name"] for c in p["commander_candidates"]}
        assert p["card_count"] == 100


async def test_save_estimates_bracket():
    p = await deckimport.preview(await importers.import_url("https://archidekt.com/decks/4242"))
    saved = await deckimport.save(name=p["name"], commanders=p["commanders"], cards=p["cards"], categories=p["categories"])
    deck = storage.load(saved["slug"])
    assert deck["bracket"] == saved["bracket"] == 2  # no Game Changers, no combos -> estimated Core
    cats = {c["name"]: c["category"] for c in deck["cards"]}
    assert cats["Sol Ring"] == "Ramp" and cats["Swamp"] == "Land" and deck["validation"]["legal"]
    with pytest.raises(ValueError, match="Commander"):
        await deckimport.save(name="x", commanders=[], cards=["1 Sol Ring"])


def test_routes():
    client = TestClient(app)
    p = client.post("/api/import/preview", json={"url": "https://tappedout.net/mtg-decks/meren-grave-fun/"}).json()
    assert p["commanders"] == ["Meren of Clan Nel Toth"] and p["card_count"] == 100
    r = client.post("/api/import", json={"name": "Aus TappedOut", "commanders": p["commanders"], "cards": p["cards"],
                                          "site": p["site"], "source": p["source"], "bracket": 3})  # fmt: skip
    assert r.status_code == 200
    deck = client.get(f"/api/decks/{r.json()['slug']}").json()
    assert deck["bracket"] == 3 and deck["imported_from"]["site"] == "TappedOut"
    assert "tappedout.net" in deck["description"]
    assert client.post("/api/import/preview", json={"url": "https://moxfield.com/decks/blocked"}).status_code == 400
    assert client.post("/api/import/preview", json={"text": ""}).status_code == 400
    assert client.post("/api/import/preview", json={"url": "https://archidekt.com/decks/999"}).status_code == 404
    t = client.post("/api/import/preview", json={"text": "1 Meren of Clan Nel Toth *CMDR*\n1 Sol Ring"}).json()
    assert t["site"] == "Liste" and t["card_count"] == 2
