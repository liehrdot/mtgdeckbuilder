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
    # tokens get the default copies, markers like The Monarch one
    assert [(c["name"], c["qty"], c["front"]["image"]["origin"]) for c in toks] == [("Treasure", 3, "mpcfill"), ("The Monarch", 1, "scryfall")]
    assert p["quantity"] == 100 + 4
    r = await proxy.prepare(deck, tokens=1)
    assert r["quantity"] == 102
    assert (await proxy.plan(deck))["quantity"] == 100  # tokens are opt-in


async def test_own_quantity_per_token(tmp_path):
    deck = await _deck()
    proxy.set_token_qty("tokens", {"Treasure": 10, "The Monarch": 0})
    p = await proxy.plan(deck, tokens=2)
    toks = {c["name"]: c for c in p["cards"] if c.get("token")}
    assert (toks["Treasure"]["qty"], toks["Treasure"]["default_qty"], toks["Treasure"]["qty_custom"]) == (10, 2, True)
    assert toks["The Monarch"]["qty"] == 0 and toks["The Monarch"]["default_qty"] == 1
    assert p["quantity"] == 110
    r = await proxy.prepare(deck, tokens=2)  # a token set to 0 gets no slot and no file
    assert r["quantity"] == 110
    names = [m["name"] for m in __import__("json").loads((proxy.order_dir("tokens") / "manifest.json").read_text("utf-8"))]
    assert names.count("Treasure") == 10 and "The Monarch" not in names
    assert not any("Monarch" in f.name for f in (proxy.order_dir("tokens") / "images").iterdir())
    # back to the default
    assert proxy.set_token_qty("tokens", {"The Monarch": None}) == {"Treasure": 10}
    assert {c["name"]: c["qty"] for c in (await proxy.plan(deck, tokens=2))["cards"] if c.get("token")}["The Monarch"] == 1


async def test_token_qty_route_and_mcp(tmp_path):
    await _deck()
    with TestClient(app) as client:
        r = client.post("/api/decks/tokens/print/token-qty", json={"counts": {"Treasure": 5}})
        assert r.json() == {"Treasure": 5}
        assert client.post("/api/decks/tokens/print/token-qty", json={"counts": {"Treasure": 500}}).status_code == 400
        assert client.post("/api/decks/sammel-abc/print/token-qty", json={"counts": {"Treasure": 1}}).status_code == 400
        plan = client.get("/api/decks/tokens/print/plan?tokens=2").json()
        assert {c["name"]: c["qty"] for c in plan["cards"] if c.get("token")} == {"Treasure": 5, "The Monarch": 1}
    res = await mcp.call_tool("create_proxy_order", {"slug": "tokens", "token_counts": {"Treasure": 7, "The Monarch": 0}})
    data = res.structured_content if hasattr(res, "structured_content") else __import__("json").loads(res[0].text)
    assert data["quantity"] == 107 and proxy.load_token_qty("tokens") == {"Treasure": 7, "The Monarch": 0}


def test_token_quantities_are_backed_up():
    import zipfile

    from mtgdeck import backup

    proxy.set_token_qty("meren", {"Human": 10})
    info = backup.create("manuell")
    with zipfile.ZipFile(backup.BACKUP_DIR / info["name"]) as z:
        assert "proxies/meren/tokens.json" in z.namelist()
    proxy._token_qty_path("meren").unlink()
    backup.restore(info["name"])
    assert proxy.load_token_qty("meren") == {"Human": 10}
