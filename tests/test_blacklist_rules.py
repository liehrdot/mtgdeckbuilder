"""Blacklist terms/rules: True Duals, cheap tutors, fast mana, price limit, 2-card combos, free text."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mtgdeck import blacklist, deckedit, mcp_server, scryfall
from mtgdeck.gui import app as gui
from mtgdeck.validate import validate_deck

from conftest import CARDS, deck_lines


def test_parse_rule_recognises_german_and_english_terms():
    assert blacklist.parse_rule("True-Duals") == "@true-duals"
    assert blacklist.parse_rule("Günstige Tutoren") == "@cheap-tutors"
    assert blacklist.parse_rule("cheap tutors") == "@cheap-tutors"
    assert blacklist.parse_rule("Fast Mana") == "@fast-mana"
    assert blacklist.parse_rule("Extrazüge") == "@extra-turns"
    assert blacklist.parse_rule("2-Karten-Combos") == "@two-card-combos"
    assert blacklist.parse_rule("teurer als 20 €") == "@price>20"
    assert blacklist.parse_rule("über 5,50 $") == "@price>5.5usd"
    assert blacklist.parse_rule("Sol Ring") is None  # a card, not a term
    assert blacklist.parse_rule("@keine Stax-Kreaturen") == "@text:keine Stax-Kreaturen"


async def test_update_stores_rules_next_to_cards():
    r = await blacklist.update(add=["Sol Ring", "True Duals", "Günstige Tutoren", "teurer als 20 €", "Gibtsnicht Karte"])
    assert r["added"] == ["Sol Ring"]
    assert [x["rule"] for x in r["added_rules"]] == ["@true-duals", "@cheap-tutors", "@price>20"]
    assert r["not_found"] == ["Gibtsnicht Karte"]
    text = blacklist.BLACKLIST_FILE.read_text("utf-8")
    assert "Sol Ring\n" in text and "@true-duals\n" in text and "@price>20\n" in text
    assert blacklist.load() == ["Sol Ring"]  # cards only
    assert [r["label"] for r in blacklist.rules()] == ["True Duals", "Günstige Tutoren", "Teurer als 20 €"]

    await blacklist.update(add=["über 50 €"])  # one price limit only: the new one replaces the old
    assert [r["rule"] for r in blacklist.rules() if r["key"] == "price"] == ["@price>50"]
    await blacklist.update(add=["@spielt nichts, was Mitspieler ihre Züge nimmt"])
    free = blacklist.rules()[-1]
    assert free["key"] == "text" and free["checked"] is False

    r = await blacklist.update(remove=["@true-duals", "Sol Ring"])
    assert r["removed"] == ["@true-duals", "Sol Ring"]
    assert "@true-duals" not in blacklist.rule_lines()


async def test_card_rules_and_filter():
    await blacklist.update(add=["Günstige Tutoren", "Fast Mana", "True Duals", "teurer als 100 €"])
    tutor = {"name": "Demonic Tutor", "type_line": "Sorcery", "cmc": 2, "oracle_text": "Search your library for a card, put that card into your hand."}
    big_tutor = {**tutor, "name": "Diabolic Tutor", "cmc": 4}
    sol = {"name": "Sol Ring", "type_line": "Artifact", "cmc": 1, "oracle_text": "{T}: Add {C}{C}.", "price_eur": "1.5"}
    pricey = {"name": "Expensive Thing", "type_line": "Artifact", "cmc": 3, "oracle_text": "", "price_eur": "250"}
    assert blacklist.card_rules(tutor) == ["Günstige Tutoren"]
    assert blacklist.card_rules(big_tutor) == []
    assert blacklist.card_rules(sol) == ["Fast Mana"]
    assert blacklist.card_rules(pricey) == ["Teurer als 100 €"]
    assert blacklist.card_rules({"name": "Bayou"}) == ["True Duals"]  # name lists work without card data
    kept = blacklist.filter_cards([tutor, big_tutor, sol, pricey, {"name": "Bayou"}, {"name": "Cultivate"}])
    assert [c["name"] for c in kept] == ["Diabolic Tutor", "Cultivate"]


async def test_validate_reports_rule_hits_and_two_card_combos():
    await blacklist.update(add=["Günstige Tutoren", "True Duals", "2-Karten-Combos"])
    res = await validate_deck(["Meren of Clan Nel Toth"], deck_lines(["1 Demonic Tutor", "1 Bayou", "1 Filler Combo A", "1 Filler Combo B"]), bracket=4)
    errs = " | ".join(res["errors"])
    assert "Blacklist-Regel „Günstige Tutoren“: Demonic Tutor" in errs
    assert "Blacklist-Regel „True Duals“: Bayou" in errs
    assert "Blacklist-Regel „2-Karten-Combos“: Filler Combo A + Filler Combo B" in errs
    assert not res["legal"]

    await blacklist.update(remove=["@cheap-tutors", "@true-duals", "@two-card-combos"])
    res = await validate_deck(["Meren of Clan Nel Toth"], deck_lines(["1 Demonic Tutor", "1 Bayou", "1 Filler Combo A", "1 Filler Combo B"]), bracket=4)
    assert not any("Blacklist" in e for e in res["errors"])


async def test_price_rule_and_search_tools_skip_rule_hits(monkeypatch):
    await blacklist.update(add=["teurer als 100 €"])
    res = await validate_deck(["Meren of Clan Nel Toth"], deck_lines(["1 Bayou"]), bracket=4)
    assert any("Teurer als 100 €“: Bayou" in e for e in res["errors"])

    async def fake_search(query, **kw):
        return {"total": 3, "cards": [CARDS["Demonic Tutor"], CARDS["Vampiric Tutor"], CARDS["Cultivate"]]}

    monkeypatch.setattr(scryfall, "search", fake_search)
    deck = {"commanders": ["Meren of Clan Nel Toth"], "cards": [], "currency": "eur"}
    assert [c["name"] for c in await deckedit.role_candidates(deck, "tutor")] == ["Demonic Tutor", "Vampiric Tutor", "Cultivate"]
    await blacklist.update(add=["Alle Tutoren"])
    assert [c["name"] for c in await deckedit.role_candidates(deck, "tutor")] == ["Cultivate"]
    assert [c["name"] for c in (await mcp_server.search_cards("otag:tutor"))["cards"]] == ["Cultivate"]


async def test_mcp_get_blacklist_and_gui_routes():
    await blacklist.update(add=["Sol Ring", "Fetchlands", "@keine Stax"])
    out = await mcp_server.get_blacklist()
    assert out["blacklist"] == ["Sol Ring"]
    fetch = next(r for r in out["rules"] if r["key"] == "fetchlands")
    assert "Polluted Delta" in fetch["cards"] and fetch["checked"]
    assert any(r["label"] == "keine Stax" and not r["checked"] for r in out["rules"])

    client = TestClient(gui.app)
    data = client.get("/api/blacklist").json()
    assert data["cards"] == ["Sol Ring"] and len(data["rules"]) == 2
    assert any(c["rule"] == "@true-duals" for c in data["catalog"])
    r = client.post("/api/blacklist", json={"add": ["Shocklands"], "remove": ["@fetchlands"]}).json()
    assert [x["label"] for x in r["added_rules"]] == ["Shocklands"]
    assert [x["key"] for x in r["rules"]] == ["text", "shocklands"]
