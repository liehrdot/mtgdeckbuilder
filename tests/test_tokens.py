"""Tokens, emblems and markers: card data, deck list, printing them with the deck."""

from conftest import MPC_SERVER, deck_lines
from fastapi.testclient import TestClient

from mtgdeck import proxy, scryfall, settings, storage
from mtgdeck.cards import deck_tokens
from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


async def _deck():
    await mcp.call_tool("save_deck", {"name": "Tokens", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines(extra=["1 Pitiless Plunderer"])]})  # fmt: skip
    return storage.load("tokens")


def test_compact_lists_tokens_and_markers_only():
    raw = {"id": "self", "name": "X", "all_parts": [
        {"id": "self", "component": "combo_piece", "name": "X", "type_line": "Creature"},
        {"id": "t1", "component": "token", "name": "Zombie", "type_line": "Token Creature — Zombie"},
        {"id": "e1", "component": "combo_piece", "name": "Chandra Emblem", "type_line": "Emblem — Chandra"},
        {"id": "m1", "component": "meld_part", "name": "Other Half", "type_line": "Legendary Creature"}]}  # fmt: skip
    assert [t["name"] for t in scryfall.compact(raw)["tokens"]] == ["Zombie", "Chandra Emblem"]


async def test_deck_tokens_merge_and_images():
    deck = await _deck()
    toks = await deck_tokens(deck["commanders"] + [c["name"] for c in deck["cards"]])
    assert [(t["name"], t["from"]) for t in toks] == [("Treasure", ["Pitiless Plunderer"]), ("The Monarch", ["Pitiless Plunderer"])]
    assert toks[0]["image"] == "https://cards.scryfall.io/normal/front/a/0/a0b0-treasure.jpg"
    assert TestClient(app).get("/api/decks/tokens/tokens").json()[0]["name"] == "Treasure"


async def test_print_plan_and_prepare_with_tokens():
    deck = await _deck()
    settings.update({"mpcfill_server": MPC_SERVER})
    p = await proxy.plan(deck, tokens=3)
    toks = [c for c in p["cards"] if c.get("token")]
    assert [(c["name"], c["qty"], c["front"]["image"]["origin"]) for c in toks] == [("Treasure", 3, "mpcfill"), ("The Monarch", 3, "scryfall")]
    assert p["quantity"] == 100 + 6
    r = await proxy.prepare(deck, tokens=1)
    assert r["quantity"] == 102
    assert (await proxy.plan(deck))["quantity"] == 100  # tokens are opt-in
