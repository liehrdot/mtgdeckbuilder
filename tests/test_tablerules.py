"""Table rules ("Tischregeln"): rule sets per playgroup, chosen per deck and checked on validation."""

from __future__ import annotations

from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import health, mcp_server, rule0, storage, tablerules
from mtgdeck.gui import app as gui
from mtgdeck.validate import validate_deck

MEREN = ["Meren of Clan Nel Toth"]


async def test_create_update_and_summary():
    rs = await tablerules.create("Freitagsrunde", add=["True Duals", "Cultivate", "teurer als 100 €", "@kein Diebstahl"],
                                 max_bracket=3, max_game_changers=1, no_proxies=True)  # fmt: skip
    assert rs["rules"] == ["@true-duals", "@price>100", "@text:kein Diebstahl"]
    assert rs["cards"] == ["Cultivate"]
    assert rs["summary"] == ["Höchstens Bracket 3", "Höchstens 1 Game Changer", "Keine Proxies",
                             "Verboten: True Duals, Teurer als 100 €", "Verbotene Karten: Cultivate", "Absprachen: kein Diebstahl"]
    out = await tablerules.update(rs["id"], remove=["@true-duals", "Cultivate"], max_game_changers=None, deck_budget=150)
    assert out["rules"] == ["@price>100", "@text:kein Diebstahl"] and out["cards"] == []
    assert out["max_game_changers"] is None and out["deck_budget"] == 150
    assert tablerules.resolve_id("freitagsrunde")["id"] == rs["id"]
    tablerules.delete(rs["id"])
    assert tablerules.all_sets() == []


async def test_validate_reports_table_rule_violations():
    rs = await tablerules.create("Laden", add=["Günstige Tutoren", "Cultivate", "2-Karten-Combos"], max_bracket=3, max_game_changers=0)
    lines = deck_lines(["1 Demonic Tutor", "1 Filler Combo A", "1 Filler Combo B"])
    res = await validate_deck(MEREN, lines, 4, table_rule=rs["id"])
    t = res["table_rule"]
    assert t["name"] == "Laden" and not t["compliant"] and not res["legal"]
    text = " | ".join(t["violations"])
    assert "höchstens Bracket 3 – das Deck ist für Bracket 4 gebaut" in text
    assert "keine Game Changer, im Deck 1: Demonic Tutor" in text
    assert "verbotene Karten Cultivate" in text
    assert "„Günstige Tutoren“ verboten – Demonic Tutor" in text
    assert "keine 2-Karten-Combos – Filler Combo A + Filler Combo B" in text
    assert all(v in res["errors"] for v in t["violations"])

    ok = await validate_deck(MEREN, deck_lines(["1 Filler X"]), 3, table_rule=rs["id"])
    errs = [e for e in ok["errors"] if "Tischregel" in e]
    assert errs == ["Tischregel „Laden“: verbotene Karten Cultivate"]  # deck_lines() always has Cultivate

    missing = await validate_deck(MEREN, deck_lines(), 3, table_rule="deadbeef")
    assert missing["table_rule"]["missing"] and any("gibt es nicht mehr" in w for w in missing["warnings"])


async def test_budget_counts_for_proxy_decks_and_no_proxies():
    rs = await tablerules.create("Budget", deck_budget=100, no_proxies=True)
    res = await validate_deck(MEREN, deck_lines(["1 Bayou"]), 3, proxy=True, table_rule=rs["id"])
    text = " | ".join(res["table_rule"]["violations"])
    assert "Deckbudget 100 €, das Deck kostet" in text
    assert "keine Proxies" in text


async def test_save_deck_keeps_table_rule_and_health_rule0():
    rs = await tablerules.create("Freitag", add=["Cultivate"])
    cards = [mcp_server.CardEntry(name=ln.split(" ", 1)[1], qty=int(ln.split(" ", 1)[0])) for ln in deck_lines()]
    out = await mcp_server.save_deck(name="Meren T", commanders=MEREN, cards=cards, bracket=3, table_rule="Freitag")
    assert out["table_rule"]["name"] == "Freitag" and not out["legal"]
    deck = storage.load("meren-t")
    assert deck["table_rule"] == rs["id"]
    # refining without table_rule keeps it
    await mcp_server.save_deck(name="Meren T", commanders=MEREN, cards=cards, bracket=3, slug="meren-t")
    assert storage.load("meren-t")["table_rule"] == rs["id"]
    loaded = await mcp_server.load_deck("meren-t")
    assert loaded["table_rule_info"]["name"] == "Freitag"
    item = next(i for i in health.check(storage.load("meren-t"))["items"] if i["key"] == "table_rule")
    assert item["status"] == "red" and "Cultivate" in item["text"]
    row = next(r for r in rule0.build(storage.load("meren-t"))["rows"] if r["label"] == "Tischregel")
    assert row["flag"] and "Freitag" in row["value"]
    # '' removes it
    await mcp_server.save_deck(name="Meren T", commanders=MEREN, cards=cards, bracket=3, slug="meren-t", table_rule="")
    assert storage.load("meren-t")["table_rule"] is None


async def test_check_all_and_revalidate():
    rs = await tablerules.create("Ohne Tutoren", max_tutors=0)
    cards = [mcp_server.CardEntry(name=ln.split(" ", 1)[1], qty=int(ln.split(" ", 1)[0])) for ln in deck_lines(["1 Vampiric Tutor"])]
    await mcp_server.save_deck(name="Tutor Deck", commanders=MEREN, cards=cards, bracket=4)
    plain = [mcp_server.CardEntry(name=ln.split(" ", 1)[1], qty=int(ln.split(" ", 1)[0])) for ln in deck_lines(["1 Filler Y"])]
    await mcp_server.save_deck(name="Plain Deck", commanders=MEREN, cards=plain, bracket=3, table_rule=rs["id"])
    rows = {r["slug"]: r for r in await tablerules.check_all(rs["id"])}
    assert not rows["tutor-deck"]["ok"] and "Vampiric Tutor" in rows["tutor-deck"]["errors"][0]
    assert rows["plain-deck"]["ok"] and rows["plain-deck"]["uses"]

    await tablerules.update(rs["id"], add=["Cultivate"])
    assert await tablerules.revalidate_decks(rs["id"]) == ["plain-deck"]
    deck = storage.load("plain-deck")
    assert not deck["validation"]["table_rule"]["compliant"]
    assert deck["version"] == 1  # re-validation alone makes no new version


def test_gui_routes_and_prompts():
    with TestClient(gui.app) as client:
        r = client.post("/api/tablerules", json={"name": "Kids", "add": ["Stax", "MLD"], "max_bracket": 2})
        assert r.status_code == 200
        tid = r.json()["id"]
        data = client.get("/api/tablerules").json()
        assert data["sets"][0]["name"] == "Kids" and data["sets"][0]["decks"] == 0
        assert any(c["rule"] == "@stax" for c in data["catalog"])
        r = client.patch(f"/api/tablerules/{tid}", json={"max_bracket": None, "remove": ["@mld"], "no_proxies": True}).json()
        assert r["max_bracket"] is None and r["rules"] == ["@stax"] and r["no_proxies"]
        assert client.post("/api/tablerules", json={"name": " "}).status_code == 400

        req = gui.BuildRequest(commander="Meren of Clan Nel Toth", table_rule=tid)
        prompt = gui.build_prompt(req)
        assert "Tischregel „Kids“" in prompt and f'table_rule="{tid}"' in prompt and "Verboten: Stax" in prompt
        assert client.post("/api/build", json={"commander": "Meren", "table_rule": "nope0000"}).status_code == 404

        storage.save({"name": "Gui Deck", "commanders": MEREN, "bracket": 3, "currency": "eur",
                      "cards": [{"name": ln.split(" ", 1)[1], "qty": int(ln.split(" ", 1)[0])} for ln in deck_lines()]})  # fmt: skip
        r = client.put("/api/decks/gui-deck/table-rule", json={"table_rule": tid}).json()
        assert r["table_rule"] == tid and r["validation"]["table_rule"]["compliant"]
        deck = client.get("/api/decks/gui-deck").json()
        assert deck["table_rule_info"]["name"] == "Kids" and deck["version"] == 2
        assert deck["history"][-1]["note"] == "Tischregel: Kids"
        assert "Tischregel „Kids“" in gui.refine_prompt(gui.RefineRequest(slug="gui-deck", request="x"), storage.load("gui-deck"))
        assert client.get(f"/api/tablerules/{tid}/decks").json()[0]["uses"]

        assert client.delete(f"/api/tablerules/{tid}").json()["decks"] == ["gui-deck"]
        deck = storage.load("gui-deck")
        assert deck["table_rule"] is None and "Kids" in deck["history"][-1]["note"]


async def test_mcp_tools():
    out = await mcp_server.update_table_rule(name="Freitag", add=["Fast Mana"], max_bracket=3)
    assert out["rules"] == ["Höchstens Bracket 3", "Verboten: Fast Mana"]
    out = await mcp_server.update_table_rule(name="freitag", max_bracket=0, max_tutors=1)
    assert out["rules"] == ["Höchstens 1 Tutor", "Verboten: Fast Mana"]
    listed = await mcp_server.table_rules()
    assert listed["count"] == 1 and listed["table_rules"][0]["name"] == "Freitag"
    assert (await mcp_server.update_table_rule(name="Freitag", delete=True))["deleted"] == "Freitag"
