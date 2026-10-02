"""Collective print orders: cards from several decks, single cards and tokens printed together."""

import time

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import collection, printorders, proxy
from mtgdeck.gui import app as app_mod
from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


async def _deck(name="Order Deck"):
    await mcp.call_tool("save_deck", {"name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    return name.lower().replace(" ", "-")


async def test_order_items_merge_and_as_deck():
    o = printorders.create("Herbst-Bestellung")
    assert o["slug"] == "sammel-" + o["id"] and printorders.is_order_slug(o["slug"]) and not printorders.is_order_slug("meren")
    r = await printorders.add(o["id"], [{"name": "sol ring", "qty": 2, "source": "Upgrades Aesi"},
                                        {"kind": "token", "name": "Treasure", "qty": 30, "token_id": "7c0d0000-0000-0000-0000-000000000001"},
                                        {"kind": "token", "name": "Treasure", "qty": 20, "token_id": "7c0d0000-0000-0000-0000-000000000001"}])  # fmt: skip
    assert r["added"] == 52 and r["counts"] == {"cards": 2, "tokens": 50, "slots": 52, "entries": 2, "mpc_bracket": 55,
                                                 "sources": ["Tokens", "Upgrades Aesi"]}  # fmt: skip
    await printorders.add(o["id"], [{"name": "Sol Ring", "qty": 1, "source": "Deck B"}])  # same card, other source
    deck = printorders.as_deck(o["id"])
    assert deck["slug"] == o["slug"] and deck["cards"] == [{"name": "Sol Ring", "qty": 3}]
    assert deck["print_tokens"][0]["qty"] == 50 and deck["print_tokens"][0]["from"] == ["Tokens"]
    with pytest.raises(ValueError, match="Nicht gefunden"):
        await printorders.add(o["id"], [{"name": "Gibt Es Nicht Karte"}])
    sol = next(i for i in printorders.load(o["id"])["items"] if i["source"] == "Deck B")
    assert printorders.update_item(o["id"], sol["id"], 0)["counts"]["cards"] == 2
    assert printorders.remove(o["id"], source="Tokens")["counts"]["tokens"] == 0


async def test_deck_items_all_selected_missing():
    slug = await _deck()
    from mtgdeck import storage

    deck = storage.load(slug)
    assert sum(i["qty"] for i in printorders.deck_items(deck)) == 100
    assert {i["name"] for i in printorders.deck_items(deck, names=["Sol Ring", "Cultivate"])} == {"Cultivate", "Sol Ring"}
    await collection.add([{"name": "Sol Ring", "qty": 1}])
    missing = printorders.deck_items(deck, only_missing=True)
    assert "Sol Ring" not in {i["name"] for i in missing} and "Forest" not in {i["name"] for i in missing}  # basics count as owned
    assert missing[0]["source"] == "Order Deck" and missing[0]["source_slug"] == slug


async def test_print_plan_for_order_with_tokens_only_and_cards():
    o = printorders.create("Nur Tokens")
    with pytest.raises(ValueError, match="füge zuerst"):
        await proxy.plan(printorders.as_deck(o["id"]))
    await printorders.add(o["id"], [{"kind": "token", "name": "Treasure", "qty": 50, "token_id": "7c0d0000-0000-0000-0000-000000000001"},
                                    {"kind": "token", "name": "Treasure", "qty": 10, "token_id": "7c0d0000-0000-0000-0000-000000000002"}])  # fmt: skip
    plan = await proxy.plan(printorders.as_deck(o["id"]), source="scryfall")
    assert [(c["name"], c["qty"], c["token"]) for c in plan["cards"]] == [("Treasure", 50, True), ("Treasure 2", 10, True)]
    assert plan["quantity"] == 60 and plan["mpc_bracket"] == 72 and not plan["missing"]
    assert "7c0d0000-0000-0000-0000-000000000002" in plan["cards"][1]["front"]["image"]["full"]
    await printorders.add(o["id"], [{"name": "Delver of Secrets // Insectile Aberration", "qty": 2, "source": "Deck A"}])
    plan = await proxy.plan(printorders.as_deck(o["id"]), source="scryfall")
    delver = plan["cards"][0]
    assert delver["qty"] == 2 and delver["back"]["face"] == "Insectile Aberration" and plan["quantity"] == 62


def test_routes_and_print_routes_reuse(monkeypatch):
    with TestClient(app) as client:  # keeps the event loop alive for the background print job
        _routes(client)


def _routes(client):
    o = client.post("/api/orders", json={"name": "Sammel"}).json()
    oid, slug = o["id"], o["slug"]
    r = client.post(f"/api/orders/{oid}/items", json={"text": "2 Sol Ring\n1 Cultivate",
                                                       "items": [{"kind": "token", "name": "Treasure", "qty": 50}]})  # fmt: skip
    assert r.status_code == 200 and r.json()["counts"] == {"cards": 3, "tokens": 50, "slots": 53, "entries": 3, "mpc_bracket": 55,
                                                           "sources": ["Liste", "Tokens"]}  # fmt: skip
    assert client.post(f"/api/orders/{oid}/items", json={}).status_code == 400
    assert client.post(f"/api/orders/{oid}/items", json={"text": "1 Gibt Es Nicht"}).status_code == 400
    assert client.get("/api/orders").json()[0]["name"] == "Sammel"
    assert client.patch(f"/api/orders/{oid}", json={"name": "Weihnachten"}).json()["name"] == "Weihnachten"
    # the print routes of decks work with the order's slug
    plan = client.get(f"/api/decks/{slug}/print/plan", params={"source": "scryfall"}).json()
    assert plan["quantity"] == 53 and {c["name"] for c in plan["cards"]} == {"Sol Ring", "Cultivate", "Treasure"}
    job = client.post(f"/api/decks/{slug}/print/prepare", json={"source": "scryfall"}).json()["job"]
    for _ in range(300):
        if any(e["type"] == "done" for e in app_mod.JOBS[job].events):
            break
        time.sleep(0.02)
    assert any(e["type"] == "print" for e in app_mod.JOBS[job].events), app_mod.JOBS[job].events
    pdf = client.post(f"/api/decks/{slug}/print/pdf", json={"paper": "A4"})
    assert pdf.status_code == 200 and pdf.json()["pages"] >= 6
    added = client.post(f"/api/decks/{slug}/collection/add-printed", json={"source": "scryfall"}).json()
    assert added["added"] == 3  # tokens are not collection entries
    assert client.get("/api/decks/sammel-00000000/print/plan").status_code == 404
    item = next(i for i in client.get(f"/api/orders/{oid}").json()["items"] if i["name"] == "Sol Ring")["id"]
    assert client.patch(f"/api/orders/{oid}/items/{item}", json={"qty": 4}).json()["counts"]["cards"] == 5
    assert client.delete(f"/api/orders/{oid}/items", params={"source": "Liste"}).json()["counts"]["cards"] == 0
    assert client.delete(f"/api/orders/{oid}").json() == {"deleted": True}
    assert not proxy.order_dir(slug).exists()
    assert client.get(f"/api/orders/{oid}").status_code == 404


def test_token_search():
    client = TestClient(app)
    hits = client.get("/api/tokens/search", params={"q": "treasure"}).json()
    assert [h["id"][-1] for h in hits] == ["1", "2"] and hits[0]["type_line"].startswith("Token")
    assert client.get("/api/tokens/search", params={"q": "zzz"}).json() == []


async def test_order_from_deck_route_and_mcp():
    slug = await _deck("Aesi Test")
    client = TestClient(app)
    oid = client.post("/api/orders", json={}).json()["id"]
    r = client.post(f"/api/orders/{oid}/items", json={"deck": slug, "names": ["Sol Ring", "Cultivate"]}).json()
    assert r["counts"]["cards"] == 2 and r["counts"]["sources"] == ["Aesi Test"]
    res = str(await mcp.call_tool("update_print_order", {"order": "Claude-Bestellung", "add_cards": [{"name": "Sol Ring", "qty": 3}],
                                                         "add_tokens": [{"name": "Treasure", "qty": 50}], "source": "Upgrades"}))  # fmt: skip
    assert "Claude-Bestellung" in res and "'slots': 53" in res
    again = str(await mcp.call_tool("update_print_order", {"order": "claude-bestellung", "from_deck": slug}))
    assert "'slots': 153" in again
    assert "Claude-Bestellung" in str(await mcp.call_tool("print_orders", {}))


async def test_added_since_a_rebuild():
    from mtgdeck import deckedit

    slug = await _deck("Umbau")
    await deckedit.edit_deck(slug, add=[{"name": "Demonic Tutor"}, {"name": "Forest", "qty": 1}], remove=["Filler 1", "Filler 2"])
    items = printorders.added_since(slug, 1)
    assert [(i["name"], i["qty"]) for i in items] == [("Demonic Tutor", 1), ("Forest", 1)]
    assert items[0]["source"] == "Umbau (neu in v2)"
    assert [i["name"] for i in printorders.added_since(slug, 1, only_missing=True)] == ["Demonic Tutor"]  # basics are owned
    assert printorders.added_since(slug, 2) == []
    with TestClient(app) as client:
        r = client.get(f"/api/decks/{slug}/added", params={"since": 1}).json()
        assert r["version"] == 2 and r["cards"] == 2
        oid = client.post("/api/orders", json={"name": "Neu"}).json()["id"]
        o = client.post(f"/api/orders/{oid}/items", json={"deck": slug, "since_version": 1, "only_missing": True}).json()
        assert o["counts"]["cards"] == 1 and o["counts"]["sources"] == ["Umbau (neu in v2)"]
        assert client.get("/api/decks/nope/added", params={"since": 1}).status_code == 404


def test_add_by_link_and_list():
    with TestClient(app) as client:
        oid = client.post("/api/orders", json={}).json()["id"]
        r = client.post(f"/api/orders/{oid}/items", json={"url": "https://tappedout.net/mtg-decks/meren-grave-fun/"}).json()
        assert r["counts"]["cards"] == 100 and r["counts"]["sources"] == ["Meren Grave Fun (TappedOut)"]
        r = client.post(f"/api/orders/{oid}/items", json={"text": "1 Sol Ring\n1 Cultivate"}).json()
        assert r["counts"]["cards"] == 102
        assert "Liste einfügen" in client.post(f"/api/orders/{oid}/items", json={"url": "https://moxfield.com/decks/blocked"}).json()["detail"]
        assert client.post(f"/api/orders/{oid}/items", json={"url": "https://archidekt.com/decks/999"}).status_code == 404
