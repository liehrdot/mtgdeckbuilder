"""Traffic-light deck check and role candidates for fixing it."""

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import deckedit, health, storage
from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


def _deck(lands=36, avg=3.0, roles=None, cards=None, combos=0, bracket=3, curve=None):
    roles = roles or {}
    return {
        "bracket": bracket,
        "cards": cards or [],
        "validation": {
            "stats": {"types": {"Land": lands}, "avg_cmc_nonland": avg, "mana_curve": curve or {"2": 20},
                      "roles": {r: [f"{r} {i}" for i in range(n)] for r, n in roles.items()}},
            "combos": {"included": [{"id": str(i)} for i in range(combos)]},
        },
    }  # fmt: skip


def _by_key(result):
    return {i["key"]: i for i in result["items"]}


def test_healthy_deck_is_green():
    cards = [{"name": f"Win {i}", "category": "Win Condition"} for i in range(3)] + [{"name": f"S{i}", "category": "Synergy"} for i in range(30)]
    r = health.check(_deck(roles={"ramp": 11, "card_draw": 10, "removal": 6, "counterspell": 3, "board_wipe": 2}, cards=cards))
    assert r["status"] == "green", r
    assert r["summary"] == "Alles im grünen Bereich."
    items = _by_key(r)
    assert items["removal"]["value"] == 9  # removal + counterspells, no double counting
    assert items["wincons"]["cards"] == ["Win 0", "Win 1", "Win 2"]
    assert all(i["why"] for i in r["items"])


def test_problems_are_yellow_or_red_with_fix():
    r = health.check(_deck(lands=31, avg=4.1, roles={"ramp": 8, "card_draw": 3}, curve={"6": 8, "7+": 7}))
    items = _by_key(r)
    assert r["status"] == "red"
    assert items["lands"]["status"] == "red" and "zu wenige" in items["lands"]["text"] and items["lands"]["target"] == "37–39"
    assert items["ramp"]["status"] == "yellow" and items["ramp"]["fix"]["role"] == "ramp"
    assert items["draw"]["status"] == "red"
    assert items["removal"]["status"] == "red" and items["removal"]["fix"]["role"] == "removal"
    assert items["wipes"]["status"] == "yellow"
    assert items["curve"]["status"] == "red" and items["curve"]["fix"]["focus"]
    # no categories at all: win conditions unknown -> yellow with a hint instead of red
    assert items["wincons"]["status"] == "yellow" and "Win Condition" in items["wincons"]["text"]


def test_land_target_depends_on_curve_and_ramp():
    assert health.land_target(2.3, 13) == 34
    assert health.land_target(3.0, 10) == 36
    assert health.land_target(3.7, 10) == 38
    assert _by_key(health.check(_deck(lands=34, avg=2.3, roles={"ramp": 13})))["lands"]["status"] == "green"


def test_combo_counts_as_win_condition_and_red_when_categorized_without():
    cards = [{"name": f"S{i}", "category": "Synergy"} for i in range(30)]
    assert _by_key(health.check(_deck(cards=cards)))["wincons"]["status"] == "red"
    assert _by_key(health.check(_deck(cards=cards, combos=2)))["wincons"]["status"] == "green"


async def test_deck_api_includes_health_and_role_candidates():
    await mcp.call_tool("save_deck", {"name": "Health", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    client = TestClient(app)
    deck = client.get("/api/decks/health").json()
    assert deck["health"]["status"] in ("yellow", "red")
    assert {i["key"] for i in deck["health"]["items"]} == {"lands", "ramp", "draw", "removal", "wipes", "curve", "wincons"}
    cands = client.get("/api/decks/health/role-candidates", params={"role": "ramp"}).json()
    assert [c["name"] for c in cands] == ["Filler Ramp Rock"]  # Sol Ring and Cultivate are already in the deck
    assert cands[0]["reason"] == "Ramp"
    assert client.get("/api/decks/health/role-candidates", params={"role": "nope"}).status_code == 400
    with pytest.raises(ValueError):
        await deckedit.role_candidates(storage.load("health"), "nope")
