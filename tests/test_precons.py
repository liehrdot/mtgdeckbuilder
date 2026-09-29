"""Precons from MTGJSON and the staged upgrade plan."""

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import precons, storage
from mtgdeck.gui import app as app_mod
from mtgdeck.gui.app import PlanRequest, _enrich_plan, app, plan_prompt
from mtgdeck.mcp_server import mcp


async def test_search_filters_commander_decks():
    items = await precons.search()
    assert [p["file"] for p in items] == ["GraveTroupe_C99", "OldGuard_C98"]  # newest first, no intro packs, broken rows skipped
    assert [p["name"] for p in await precons.search("grave 2024")] == ["Grave Troupe"]
    assert await precons.search("c98") == [items[1]]


async def test_load_and_import():
    p = await precons.load("GraveTroupe_C99")
    assert p["commanders"] == ["Meren of Clan Nel Toth"] and p["card_count"] == 100
    with pytest.raises(ValueError):
        await precons.load("../etc/passwd")
    saved = await precons.import_precon("GraveTroupe_C99")
    deck = storage.load(saved["slug"])
    assert deck["bracket"] == 2 and deck["precon"]["code"] == "C99" and deck["validation"]["stats"]["card_count"] == 99 and deck["validation"]["legal"]
    cats = {c["name"]: c["category"] for c in deck["cards"]}
    assert cats["Forest"] == "Land" and cats["Cultivate"] == "Ramp"
    assert deck["history"][0]["note"] == "Importiert: Precon „Grave Troupe“"
    again = await precons.import_precon("GraveTroupe_C99")
    assert again["slug"] != saved["slug"]  # a second import does not overwrite the first


def test_routes():
    client = TestClient(app)
    assert client.get("/api/precons", params={"q": "old"}).json()[0]["code"] == "C98"
    p = client.get("/api/precons/GraveTroupe_C99").json()
    assert p["commander_images"]["Meren of Clan Nel Toth"]
    assert client.get("/api/precons/Missing_X").status_code == 404
    r = client.post("/api/precons/import", json={"file": "GraveTroupe_C99"})
    assert r.status_code == 200 and client.get(f"/api/decks/{r.json()['slug']}").json()["precon"]["name"] == "Grave Troupe"


async def test_mcp_precon_tools():
    assert "Grave Troupe" in str(await mcp.call_tool("search_precons", {"query": "grave"}))
    assert "grave-troupe" in str(await mcp.call_tool("import_precon", {"file": "GraveTroupe_C99"}))
    assert "error" in str(await mcp.call_tool("import_precon", {"file": "bad/name"}))


async def _deck():
    await mcp.call_tool("save_deck", {"name": "Plan", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    return storage.load("plan")


async def test_plan_prompt_and_enrich():
    deck = await _deck()
    prompt = plan_prompt(deck, PlanRequest(stages=[100, 20, 50]), False)
    assert "Stufe 1 bis 20 EUR insgesamt, Stufe 2 bis 50 EUR insgesamt, Stufe 3 bis 100 EUR insgesamt" in prompt
    plan = await _enrich_plan(deck, {"summary": "S", "stages": [
        {"title": "Ramp", "upgrades": [{"add": "Vampiric Tutor", "remove": "Filler 1", "reason": "r"}]},
        {"title": "Draw", "upgrades": [{"add": "Vampiric Tutor", "remove": "Filler 2", "reason": "doppelt"},
                                       {"add": "Necropotence", "remove": "Filler 1", "reason": "schon entfernt"},
                                       {"add": "Demonic Tutor", "remove": "Filler 3", "reason": "ok"}]},
        {"title": "Leer", "upgrades": [{"add": "Gibt Es Nicht", "remove": "Filler 4", "reason": "x"}]},
    ]}, [20, 50, 100])  # fmt: skip
    assert [s["title"] for s in plan["stages"]] == ["Ramp", "Draw"]
    assert [u["add"] for u in plan["stages"][1]["upgrades"]] == ["Demonic Tutor"]
    assert plan["stages"][0]["budget"] == 20 and plan["stages"][0]["cost"] == 0.5
    assert await _enrich_plan(deck, {"stages": []}, [20]) is None


async def test_plan_job_stores_plan(monkeypatch):
    await _deck()

    async def fake_run(job, prompt, model, output_format=None, *, read_only=False, finish=None):
        assert read_only and "stages" in output_format["schema"]["properties"]
        await finish(job, True, "", {"stages": [{"title": "Eins", "upgrades": [{"add": "Necropotence", "remove": "Filler 5", "reason": "r"}]}]})
        job.done = True

    monkeypatch.setattr(app_mod, "_run_claude", fake_run)
    client = TestClient(app)
    job = client.post("/api/decks/plan/upgrade-plan", json={"stages": [30]}).json()["job"]
    import time
    for _ in range(50):
        if any(e["type"] == "done" for e in app_mod.JOBS[job].events):
            break
        time.sleep(0.02)
    assert any(e["type"] == "plan" for e in app_mod.JOBS[job].events)
    assert client.get("/api/decks/plan").json()["upgrade_plan"]["stages"][0]["budget"] == 30
