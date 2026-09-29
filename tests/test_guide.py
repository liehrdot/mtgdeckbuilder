"""Rule-0 text and the stored play guide."""

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import rule0, storage
from mtgdeck.gui import app as app_mod
from mtgdeck.gui.app import _clean_guide, app, guide_prompt
from mtgdeck.mcp_server import mcp


async def _save(name="Guide", extra=None, **kw):
    await mcp.call_tool("save_deck", {"name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": 3,
        "description": "Opfern und zurückholen. Meren holt jede Runde etwas zurück.",
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines(extra=extra)], **kw})  # fmt: skip
    return storage.load(storage.slug(name))


async def test_rule0_rows_and_text():
    deck = await _save(extra=["1 Demonic Tutor", "1 Time Warp"], fillers=None, power_profile={"tier": "high", "allow_two_card_combos": False})
    r = rule0.build(deck)
    rows = {x["label"]: x for x in r["rows"]}
    assert rows["Stufe"]["value"].startswith("oberes Bracket 3 (Upgraded)")
    assert rows["Spielweise"]["value"] == "Opfern und zurückholen."
    assert rows["Game Changer"]["value"] == "1: Demonic Tutor" and rows["Game Changer"]["flag"]
    assert rows["Extra Turns"]["value"] == "Time Warp" and rows["Extra Turns"]["flag"]
    assert rows["2-Karten-Combos"]["value"] == "keine"
    assert rows["Hausregeln"]["value"] == "keine 2-Karten-Combos"
    assert rows["Tempo"]["value"].endswith("Zug 6+")
    assert r["text"].startswith("Guide – Rule 0\nStufe: ") and r["text"].endswith("Passt das für eure Runde?")
    assert "Proxys" not in rows


async def test_rule0_route_and_proxy():
    await _save("Proxy Guide", proxy=True)
    r = TestClient(app).get("/api/decks/proxy-guide/rule0").json()
    assert any(x["label"] == "Proxys" for x in r["rows"])
    assert TestClient(app).get("/api/decks/nope/rule0").status_code == 404


async def test_clean_guide_filters_and_stores_without_new_version():
    deck = await _save()
    guide = await _clean_guide(deck, {
        "plan": "Ramp, dann Meren.", "early": ["[[Sol Ring]] spielen", ""], "mid": ["Meren"], "late": ["Drain"],
        "mulligan": ["3 Länder"], "win_conditions": ["Aristocrats"],
        "key_cards": [{"name": "Sol Ring", "why": "Ramp"}, {"name": "Necropotence", "why": "nicht im Deck"}],
    })  # fmt: skip
    assert guide["early"] == ["[[Sol Ring]] spielen"] and guide["watch_out"] == []
    assert [k["name"] for k in guide["key_cards"]] == ["Sol Ring"] and guide["key_cards"][0]["image"]
    assert guide["cards"]["Sol Ring"]["image"] and guide["version"] == 1
    assert await _clean_guide(deck, {"plan": ""}) is None
    storage.set_extra("guide", "guide", guide)
    loaded = storage.load("guide")
    assert loaded["guide"]["plan"] == "Ramp, dann Meren." and loaded["version"] == 1 and len(storage.versions("guide")) == 1
    with pytest.raises(ValueError):
        storage.set_extra("guide", "cards", [])
    assert "load_deck" in guide_prompt(deck)


async def test_guide_job_stores_structured_output(monkeypatch):
    await _save()

    async def fake_run(job, prompt, model, output_format=None, *, read_only=False, finish=None):
        assert read_only and output_format["schema"]["required"][0] == "plan"
        await finish(job, True, "", {"plan": "Plan.", "early": [], "mid": [], "late": [], "mulligan": [], "win_conditions": [],
                                     "key_cards": [{"name": "Cultivate", "why": "Ramp"}]})  # fmt: skip
        job.done = True

    monkeypatch.setattr(app_mod, "_run_claude", fake_run)
    client = TestClient(app)
    job = client.post("/api/decks/guide/guide", json={}).json()["job"]
    events = []
    for _ in range(50):
        events = app_mod.JOBS[job].events
        if any(e["type"] == "done" for e in events):
            break
        import time; time.sleep(0.02)
    assert any(e["type"] == "guide" for e in events)
    assert storage.load("guide")["guide"]["key_cards"][0]["name"] == "Cultivate"
    assert client.get("/api/decks/guide").json()["guide"]["plan"] == "Plan."
