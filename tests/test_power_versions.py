"""Power profile (sub-tiers, house rules, score) and deck versioning (history, diff, restore, copy)."""

import json

from conftest import CARDS, deck_lines
from fastapi.testclient import TestClient

from mtgdeck import brackets, power, storage
from mtgdeck.gui import app as gui
from mtgdeck.mcp_server import mcp
from mtgdeck.power import PowerProfile
from mtgdeck.validate import validate_deck

MEREN = ["Meren of Clan Nel Toth"]
GCS = ["1 Demonic Tutor", "1 Vampiric Tutor", "1 Necropotence"]


def _payload(result):
    return json.loads(result.content[0].text)


def _cards(lines):
    return [{"name": ln.split(" ", 1)[1], "qty": int(ln.split(" ", 1)[0])} for ln in lines]


# --- power ------------------------------------------------------------------------------------


def test_labels_and_steps():
    assert power.label(3.8)["text"] == "oberes Bracket 3"
    assert power.label(4.1) | {} == {"value": 4.1, "bracket": 4, "tier": "low", "text": "unteres Bracket 4"}
    assert power.step(3, "high", +1) == (4, "low")
    assert power.step(4, "low", -1) == (3, "high")
    assert power.step(1, "low", -1) == (1, "low")  # clamped
    assert power.step(5, "high", +1) == (5, "high")


def test_fast_mana_detection():
    assert power._is_fast_mana(CARDS["Sol Ring"])
    assert not power._is_fast_mana(CARDS["Cultivate"])
    assert not power._is_fast_mana(CARDS["Forest"])
    assert power._is_fast_mana({"type_line": "Land", "oracle_text": "{T}: Add {C}{C}. Ancient Tomb deals 2 damage to you."})
    assert not power._is_fast_mana({"type_line": "Artifact", "cmc": 2, "oracle_text": "{T}: Add one mana of any color."})


async def test_score_rises_with_game_changers():
    base = await validate_deck(MEREN, deck_lines(), 4)
    strong = await validate_deck(MEREN, deck_lines(GCS + ["1 Survival of the Fittest"]), 4)
    assert strong["bracket"]["power"]["value"] > base["bracket"]["power"]["value"]
    assert strong["bracket"]["power"]["value"] >= 4  # 4 Game Changers -> at least bracket 4
    assert any("Game Changer" in c["reason"] for c in strong["bracket"]["power"]["components"])


async def test_house_rules_are_violations():
    profile = PowerProfile(max_game_changers=0, max_tutors=1, allow_two_card_combos=False)
    lines = deck_lines(GCS[:1] + ["1 Filler Combo A", "1 Filler Combo B"])
    r = await validate_deck(MEREN, lines, 4, profile=profile)
    v = "\n".join(r["bracket"]["violations"])
    assert not r["bracket"]["compliant"]
    assert "max. 0 Game Changer" in v and "keine 2-Karten-Combos" in v
    # bracket 4 alone would be fine
    assert (await validate_deck(MEREN, lines, 4))["bracket"]["compliant"]


def test_looser_house_rule_only_warns_and_tier_mismatch():
    rules = brackets.BY_NUMBER[3]
    facts = dict(game_changers=[], tutors=[], two_card_combos=[], extra_turns=[], mass_land_denial=[])
    v, w = power.check_profile(PowerProfile(max_game_changers=5, tier="high"), rules, **facts, power=power.label(3.1))
    assert v == []
    assert any("lockerer als Bracket 3" in x for x in w)
    assert any("schwächer als gewünscht" in x for x in w)


def test_profile_prompt_lines():
    lines = gui.profile_lines(4, PowerProfile(tier="low", max_game_changers=0, style="witzig"))
    text = "\n".join(lines)
    assert "unteres Bracket 4" in text and "keine Game Changer" in text and "witzig" in text
    assert gui.profile_lines(3, PowerProfile()) == []


# --- versions --------------------------------------------------------------------------------


async def _save(name="Meren", lines=None, bracket=3, **kw):
    return _payload(await mcp.call_tool("save_deck", {
        "name": name, "commanders": MEREN, "cards": _cards(lines or deck_lines()), "bracket": bracket, **kw,
    }))  # fmt: skip


async def test_versions_diff_restore_copy():
    await _save()
    await _save(lines=deck_lines(GCS[:2]), bracket=4, slug="meren", change_note="Hoch auf 4",
                power_profile={"tier": "low"})  # fmt: skip
    vs = storage.versions("meren")
    assert [v["version"] for v in vs] == [1, 2]
    assert vs[0]["note"] == "Erstellt" and vs[1]["note"] == "Hoch auf 4"
    assert vs[1]["from"] == "Bracket 3" and vs[1]["to"] == "unteres Bracket 4"
    assert set(vs[1]["added"]) == {"Demonic Tutor", "Vampiric Tutor"}
    assert vs[1]["current"] and vs[1]["power"] is not None

    cmp = storage.compare("meren", 1)
    assert set(cmp["added"]) == {"Demonic Tutor", "Vampiric Tutor"} and cmp["removed"] == ["Filler 59", "Filler 60"]
    assert cmp["level"] == {"from": "Bracket 3", "to": "unteres Bracket 4"}

    # re-saving identical content does not create a version
    deck = storage.load("meren")
    storage.save(deck)
    assert len(storage.versions("meren")) == 2

    restored = storage.restore("meren", 1)
    assert restored["version"] == 3
    cur = storage.load("meren")
    assert cur["bracket"] == 3 and "Demonic Tutor" not in {c["name"] for c in cur["cards"]}
    assert cur["history"][-1]["note"] == "Version 1 wiederhergestellt"
    assert storage.load_version("meren", 2)["bracket"] == 4  # old versions stay

    copy = storage.copy("meren", "Meren B4", version=2)
    assert copy["slug"] == "meren-b4" and copy["version"] == 1
    c = storage.load("meren-b4")
    assert c["bracket"] == 4 and c["copied_from"] == {"slug": "meren", "version": 2}
    assert "Kopie von" in c["history"][0]["note"]
    assert storage.copy("meren")["slug"] == "meren-kopie"
    assert storage.unique_slug("Meren") == "meren-2"

    storage.delete("meren")
    assert not (storage.DECKS_DIR / ".versions" / "meren").exists()


def test_legacy_deck_without_versions_is_preserved():
    storage.DECKS_DIR.mkdir(parents=True)
    legacy = {"name": "Old", "slug": "old", "commanders": MEREN, "bracket": 2, "cards": [{"name": "Sol Ring", "qty": 1}]}
    (storage.DECKS_DIR / "old.json").write_text(json.dumps(legacy))
    storage.save({**legacy, "cards": [{"name": "Cultivate", "qty": 1}]})
    vs = storage.versions("old")
    assert [v["version"] for v in vs] == [1, 2]
    assert storage.load_version("old", 1)["cards"] == [{"name": "Sol Ring", "qty": 1}]


async def test_version_tools_and_gui_routes(monkeypatch):
    await _save()
    await _save(lines=deck_lines(GCS[:1]), slug="meren", change_note="+Tutor")
    r = _payload(await mcp.call_tool("compare_deck_versions", {"slug": "meren", "from_version": 1}))
    assert r["added"] == ["Demonic Tutor"]
    r = _payload(await mcp.call_tool("restore_deck_version", {"slug": "meren", "version": 1}))
    assert r["version"] == 3 and r["diff_to_before"]["removed"] == ["Demonic Tutor"]
    text = (await mcp.call_tool("export_deck", {"slug": "meren", "version": 2})).content[0].text
    assert "1 Demonic Tutor" in text
    r = _payload(await mcp.call_tool("copy_deck", {"slug": "meren", "new_name": "Meren Spaß"}))
    assert r["slug"] == "meren-spa"

    client = TestClient(gui.app)
    vs = client.get("/api/decks/meren/versions").json()
    assert [v["version"] for v in vs] == [1, 2, 3] and vs[-1]["current"]
    assert "1 Demonic Tutor" in client.get("/api/decks/meren/versions/2").json()["export_text"]
    assert client.get("/api/decks/meren/diff?a=2&b=3").json()["removed"] == ["Demonic Tutor"]
    assert client.post("/api/decks/meren/versions/2/restore").json()["version"] == 4
    assert client.post("/api/decks/meren/copy", json={"name": "Neu", "version": 1}).json()["slug"] == "neu"
    assert client.get("/api/decks/meren/versions/99").status_code == 404
    assert client.get("/api/decks/meren").json()["version"] == 4

    started = {}
    monkeypatch.setattr(gui, "_start", lambda prompt, model, output_format=None: started.update(p=prompt) or {"job": "x"})
    body = {"slug": "meren", "bracket": 4, "profile": {"tier": "low", "max_game_changers": 0}}
    assert client.post("/api/retune", json=body).json() == {"job": "x"}
    assert "unteres Bracket 4" in started["p"] and "keine Game Changer" in started["p"] and "change_note" in started["p"]
