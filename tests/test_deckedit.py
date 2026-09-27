"""Editing saved decks directly (no Claude run) and replacement suggestions."""

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import deckedit, storage
from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


async def _deck(name="Edit Deck"):
    await mcp.call_tool("save_deck", {
        "name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0]), "category": "Synergy"} for l in deck_lines()],
    })  # fmt: skip
    return storage.slug(name)


async def test_edit_add_remove_qty_category_creates_version():
    slug = await _deck()
    r = await deckedit.edit_deck(slug, add=[{"name": "sol ring"}], remove=["Filler 0"],
                                 set_qty={"Forest": 17, "Swamp": 18}, set_category={"Cultivate": "Ramp"}, note="Test")  # fmt: skip
    deck = storage.load(slug)
    names = {c["name"]: c for c in deck["cards"]}
    assert "Filler 0" not in names and names["Forest"]["qty"] == 17 and names["Cultivate"]["category"] == "Ramp"
    assert names["Sol Ring"]["qty"] == 2  # was already in the deck: quantities add up
    assert r["version"] == 2 and r["change_note"].startswith("Test (Manuell: + Sol Ring; − Filler 0; Forest 18→17")
    assert deck["validation"]["stats"]["card_count"] == 98  # re-validated after the edit
    assert storage.versions(slug)[-1]["removed"] == ["Filler 0", "Forest"]  # one Forest less


async def test_new_card_gets_category_from_its_role_and_errors():
    slug = await _deck()
    await deckedit.edit_deck(slug, add=[{"name": "Demonic Tutor"}, {"name": "Forest", "qty": 1}], remove=["Filler 1", "Filler 2"])
    names = {c["name"]: c for c in storage.load(slug)["cards"]}
    assert names["Demonic Tutor"]["category"] == "Synergy" and names["Forest"]["qty"] == 19
    with pytest.raises(ValueError, match="nicht gefunden"):
        await deckedit.edit_deck(slug, add=[{"name": "Gibt Es Nicht Karte"}])
    with pytest.raises(ValueError, match="Commander"):
        await deckedit.edit_deck(slug, add=[{"name": "Meren of Clan Nel Toth"}])
    with pytest.raises(ValueError, match="Keine Änderung"):
        await deckedit.edit_deck(slug, remove=["Not In Deck"])


async def test_similar_cards_excludes_deck_and_blacklist():
    slug = await _deck()
    out = await deckedit.similar_cards(storage.load(slug), "Sol Ring")
    names = [c["name"] for c in out]
    assert names == ["Filler Ramp Rock"]  # Cultivate/Sol Ring are already in the deck
    assert out[0]["reason"].startswith("gleiche Rolle")


async def test_edit_routes_and_mcp_tool():
    slug = await _deck()
    client = TestClient(app)
    r = client.post(f"/api/decks/{slug}/cards", json={"add": [{"name": "Demonic Tutor"}], "remove": ["Filler 3"]})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.post(f"/api/decks/{slug}/cards", json={"add": [{"name": "Nope Nope"}]}).status_code == 400
    assert client.post("/api/decks/nope/cards", json={"remove": ["x"]}).status_code == 404
    sim = client.get(f"/api/decks/{slug}/similar", params={"card": "Sol Ring"}).json()
    assert sim[0]["name"] == "Filler Ramp Rock"
    res = await mcp.call_tool("edit_deck", {"slug": slug, "add": [{"name": "Vampiric Tutor"}], "remove": ["Filler 4"], "change_note": "mehr Tutoren"})
    assert "mehr Tutoren" in res.content[0].text
    assert storage.load(slug)["version"] == 3
