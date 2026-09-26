from conftest import deck_lines

from mtgdeck.validate import validate_deck

MEREN = ["Meren of Clan Nel Toth"]


async def test_legal_deck_bracket_2():
    r = await validate_deck(MEREN, deck_lines(), 2)
    assert r["legal"], r["errors"]
    assert r["bracket"]["compliant"], r["bracket"]["violations"]
    assert r["stats"]["card_count"] == 99
    assert r["stats"]["types"]["Land"] == 36
    assert r["color_identity"] == "BG"


async def test_game_changer_limits():
    gcs = ["1 Demonic Tutor", "1 Vampiric Tutor", "1 Necropotence", "1 Survival of the Fittest"]
    r2 = await validate_deck(MEREN, deck_lines(gcs[:1]), 2)
    assert r2["legal"]
    assert not r2["bracket"]["compliant"]
    assert "Demonic Tutor" in r2["bracket"]["game_changers"]

    r3 = await validate_deck(MEREN, deck_lines(gcs[:3]), 3)
    assert r3["bracket"]["compliant"]
    r3b = await validate_deck(MEREN, deck_lines(gcs), 3)
    assert not r3b["bracket"]["compliant"]
    r4 = await validate_deck(MEREN, deck_lines(gcs), 4)
    assert r4["bracket"]["compliant"]


async def test_illegal_cards_are_reported():
    lines = deck_lines(["1 Armageddon", "1 Time Warp", "1 Hullbreacher", "2 Filler Dup", "1 Totally Unknown Card"])
    r = await validate_deck(MEREN, lines, 4)
    text = "\n".join(r["errors"])
    assert not r["legal"]
    assert "Armageddon" in text and "Farbidentität" in text
    assert "Hullbreacher" in text and "nicht legal" in text
    assert "Filler Dup ist 2×" in text
    assert "Totally Unknown Card" in text


async def test_card_count_and_any_number_cards():
    r = await validate_deck(MEREN, deck_lines(fillers=50), 2)
    assert any("Karten (inkl. Commander)" in e for e in r["errors"])
    lines = deck_lines(["20 Relentless Rats"], fillers=99 - 36 - 22)
    r = await validate_deck(MEREN, lines, 2)
    assert r["legal"], r["errors"]


async def test_two_card_combo_and_spellbook_estimate():
    lines = deck_lines(["1 Filler Combo A", "1 Filler Combo B"])
    r2 = await validate_deck(MEREN, lines, 2)
    assert not r2["bracket"]["compliant"]
    assert r2["bracket"]["two_card_combos"][0]["cards"] == ["Filler Combo A", "Filler Combo B"]
    assert r2["bracket"]["estimate_source"] == "Commander Spellbook"
    r3 = await validate_deck(MEREN, lines, 3)
    assert r3["bracket"]["compliant"]
    assert r3["bracket"]["warnings"]


async def test_extra_turns_and_mld_rules():
    from conftest import CARDS

    from mtgdeck import brackets

    cards = [dict(CARDS["Time Warp"], oracle_text=CARDS["Time Warp"]["oracle_text"]), dict(CARDS["Armageddon"])]
    r1 = brackets.evaluate(1, cards)
    assert len(r1["violations"]) == 2
    r3 = brackets.evaluate(3, cards)
    assert any("Mass Land Denial" in v for v in r3["violations"])
    assert r3["warnings"]
    assert brackets.evaluate(4, cards)["compliant"]
