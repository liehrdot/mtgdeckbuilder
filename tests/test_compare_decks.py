"""Compare two saved decks (a rebuild vs. the old deck) and order the difference."""

from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import collection, printorders, storage
from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


def _cards(lines):
    return [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in lines]


async def _decks():
    await mcp.call_tool("save_deck", {"name": "Meren Alt", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
                                      "cards": _cards(deck_lines())})  # fmt: skip
    new = deck_lines(extra=["1 Demonic Tutor", "1 Pitiless Plunderer"], fillers=58)
    new[new.index("18 Forest")] = "19 Forest"
    await mcp.call_tool("save_deck", {"name": "Meren Neu", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
                                      "cards": _cards(new)})  # fmt: skip
    return "meren-neu", "meren-alt"


async def test_compare_lists_new_free_and_shared_cards():
    new, old = await _decks()
    await collection.add([{"name": "Demonic Tutor", "qty": 1}])
    diff = printorders.compare(new, old)
    added = {i["name"]: i for i in diff["added"]}
    assert set(added) == {"Demonic Tutor", "Pitiless Plunderer", "Forest"}
    assert added["Demonic Tutor"]["owned"] == 1 and added["Demonic Tutor"]["missing"] == 0
    assert added["Pitiless Plunderer"]["missing"] == 1 and added["Forest"]["basic"] and added["Forest"]["qty"] == 1
    assert added["Forest"]["missing"] == 0  # basic lands count as owned
    assert [i["name"] for i in diff["removed"]] == ["Filler 58", "Filler 59", "Filler 60"]
    assert diff["totals"] == {"added": 3, "missing": 1, "removed": 3, "common": 97}
    assert diff["deck"]["name"] == "Meren Neu" and diff["other"]["slug"] == old

    items = printorders.compare_items(new, old, only_missing=True)
    assert items == [{"kind": "card", "name": "Pitiless Plunderer", "qty": 1, "source": "Meren Neu (statt Meren Alt)",
                      "source_slug": new}]  # fmt: skip
    assert [i["name"] for i in printorders.compare_items(new, old, names=["Forest", "Demonic Tutor"])] == ["Demonic Tutor", "Forest"]


async def test_best_match_prefers_the_deck_it_was_copied_from():
    new, old = await _decks()
    copy = storage.copy(old, "Meren Umbau")["slug"]
    cands = printorders.compare_candidates(copy)
    assert cands[0]["slug"] == old and cands[0]["copied_from"]
    assert cands[0]["common"] == 100
    assert [c["common"] for c in printorders.compare_candidates(new)] == [97, 97]


async def test_compare_route_and_order_the_difference():
    new, old = await _decks()
    with TestClient(app) as client:
        r = client.get(f"/api/decks/{new}/compare").json()  # best match without `other`
        assert r["other"]["slug"] == old and r["candidates"][0]["slug"] == old
        assert next(i for i in r["added"] if i["name"] == "Pitiless Plunderer")["image"]
        assert client.get(f"/api/decks/{new}/compare?other={new}").status_code == 400
        assert client.get(f"/api/decks/{new}/compare?other=gibts-nicht").status_code == 404

        order = client.post("/api/orders", json={"name": "Umbau"}).json()
        res = client.post(f"/api/orders/{order['id']}/items", json={"deck": new, "compare_with": old}).json()
        assert res["added"] == 3
        names = {i["name"]: i["qty"] for i in printorders.load(order["id"])["items"]}
        assert names == {"Demonic Tutor": 1, "Pitiless Plunderer": 1, "Forest": 1}
        # a single card from the card view
        client.post(f"/api/orders/{order['id']}/items", json={"items": [{"kind": "card", "name": "Sol Ring", "qty": 2, "source": "Einzelkarten"}]})
        assert {i["name"]: i["qty"] for i in printorders.load(order["id"])["items"]}["Sol Ring"] == 2


async def test_mcp_compare_and_order():
    new, old = await _decks()
    res = await mcp.call_tool("compare_decks", {"slug": new})
    data = res.structured_content if hasattr(res, "structured_content") else __import__("json").loads(res[0].text)
    assert data["other"]["slug"] == old and data["totals"]["common"] == 97 and data["totals"]["added"] == 3
    res = await mcp.call_tool("update_print_order", {"order": "Umbau Meren", "from_deck": new, "compare_with": old})
    data = res.structured_content if hasattr(res, "structured_content") else __import__("json").loads(res[0].text)
    assert data["added"] == 3


async def test_order_exports_as_moxfield_list():
    new, old = await _decks()
    order = printorders.create("Export")
    await printorders.add(order["id"], [
        {"kind": "card", "name": "Sol Ring", "qty": 1, "source": "A"},
        {"kind": "card", "name": "Sol Ring", "qty": 2, "source": "B"},
        {"kind": "card", "name": "Cultivate", "qty": 1, "source": "A"},
        {"kind": "token", "name": "Treasure", "qty": 10, "source": "Tokens"},
    ])  # fmt: skip
    assert printorders.to_moxfield(order["id"]) == ("1 Cultivate\n3 Sol Ring\n", 1)
    with TestClient(app) as client:
        r = client.get(f"/api/orders/{order['id']}/export")
        assert r.text == "1 Cultivate\n3 Sol Ring\n" and r.headers["x-tokens-left-out"] == "1"
        d = client.get(f"/api/orders/{order['id']}/export?download=1")
        assert "attachment" in d.headers["content-disposition"] and "Export-moxfield.txt" in d.headers["content-disposition"]
        assert client.get("/api/orders/nope/export").status_code == 404
