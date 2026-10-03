import json

from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import storage
from mtgdeck.gui.app import BuildRequest, app, build_prompt
from mtgdeck.mcp_server import mcp


def _payload(result):
    return json.loads(result.content[0].text)


async def test_tools_registered():
    names = {t.name for t in await mcp.list_tools()}
    assert {"search_cards", "local_card_search", "edhrec_recommendations", "validate_deck", "save_deck", "find_combos"} <= names


async def test_edhrec_tool():
    r = _payload(await mcp.call_tool("edhrec_recommendations", {"commander": "Meren of Clan Nel Toth"}))
    assert r["themes"][0]["slug"] == "reanimator"
    assert r["categories"][0]["cards"][0] == {"name": "Sol Ring", "synergy": 0.01, "inclusion_pct": 90.0, "num_decks": 900}
    missing = _payload(await mcp.call_tool("edhrec_recommendations", {"commander": "Nobody"}))
    assert "error" in missing


async def test_save_load_export_roundtrip():
    cards = [{"name": line.split(" ", 1)[1], "qty": int(line.split(" ", 1)[0]), "category": "Land" if "Forest" in line or "Swamp" in line else "Synergy"} for line in deck_lines()]
    r = _payload(await mcp.call_tool("save_deck", {
        "name": "Meren Test", "commanders": ["Meren of Clan Nel Toth"], "cards": cards, "bracket": 2,
        "description": "Test",
    }))  # fmt: skip
    assert r["legal"] and r["bracket"]["compliant"], r
    assert r["card_count"] == 100
    assert r["saved"]["slug"] == "meren-test"

    deck = _payload(await mcp.call_tool("load_deck", {"slug": "meren-test"}))
    assert deck["validation"]["legal"] is True
    assert {"name": "Forest", "qty": 18, "category": "Land"} in deck["cards"]

    text = (await mcp.call_tool("export_deck", {"slug": "meren-test"})).content[0].text
    assert text.startswith("1 Meren of Clan Nel Toth *CMDR*")
    assert (storage.DECKS_DIR / "meren-test.txt").exists()


async def test_gui_routes():
    await mcp.call_tool("save_deck", {
        "name": "Gui Deck", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "cards": [{"name": line.split(" ", 1)[1], "qty": int(line.split(" ", 1)[0])} for line in deck_lines()],
    })  # fmt: skip
    client = TestClient(app)
    assert len(client.get("/api/brackets").json()) == 5
    decks = client.get("/api/decks").json()
    assert decks[0]["slug"] == "gui-deck" and decks[0]["valid"] is True
    d = client.get("/api/decks/gui-deck").json()
    assert d["card_data"]["Sol Ring"]["image"].split("?")[0].endswith("Sol Ring.jpg")
    assert "18 Forest" in d["export_text"]
    assert client.post("/api/decks/gui-deck/validate").json()["legal"] is True
    assert client.get("/").status_code == 200
    trash_id = client.delete("/api/decks/gui-deck").json()["trash_id"]
    assert client.get("/api/decks/gui-deck").status_code == 404
    assert client.get("/api/trash").json()[0]["name"] == "Gui Deck"
    assert client.post(f"/api/trash/{trash_id}/restore").json()["slug"] == "gui-deck"
    assert client.get("/api/decks/gui-deck").status_code == 200


def test_build_prompt():
    p = build_prompt(BuildRequest(commander="Meren of Clan Nel Toth", bracket=2, budget=100, strategy="Aristocrats"))
    assert "commander-deckbuilder" in p and "Bracket: 2 (Core)" in p and "max. 100 EUR" in p and "Aristocrats" in p


def test_deck_view_has_back_faces():
    storage.save({"name": "Dfc Deck", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
                  "cards": [{"name": "Delver of Secrets // Insectile Aberration", "qty": 1}, {"name": "Sol Ring", "qty": 1}]})  # fmt: skip
    d = TestClient(app).get("/api/decks/dfc-deck").json()
    delver = d["card_data"]["Delver of Secrets // Insectile Aberration"]
    assert delver["image_back"].endswith("/back/delver.jpg") and delver["layout"] == "transform"
    assert d["card_data"]["Sol Ring"]["image_back"] is None
