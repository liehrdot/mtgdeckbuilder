"""Phone app ("Am Tisch"): the read model and how the sync server serves the app."""

from fastapi.testclient import TestClient

from mtgdeck.sync import appdata
from mtgdeck.sync.server import create_app


def _docs():
    deck = {"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3, "power_profile": {"tier": "high"}, "version": 2,
            "description": "Opfern und zurückholen. Mehr Text.", "updated": "2026-10-01T10:00:00+00:00",
            "cards": [{"name": "Sol Ring", "qty": 1, "category": "Ramp"}, {"name": "Forest", "qty": 30, "category": "Land"}],
            "guide": {"plan": "Opfern.", "early": ["Ramp"], "key_cards": [{"name": "Sol Ring", "why": "Mana", "image": "x"}]},
            "validation": {"legal": True, "color_identity": "BG", "bracket": {"game_changers": ["Demonic Tutor"], "power": {"value": 3.7}}}}  # fmt: skip
    games = [{"id": "g1", "played": "2026-10-02T20:00:00+00:00", "result": "win", "opponents": ["Atraxa, Praetors' Voice"], "opponent_ids": ["o1"]},
             {"id": "g2", "played": "2026-10-03T20:00:00+00:00", "result": "loss", "opponents": ["Krenko, Mob Boss"]}]  # fmt: skip
    opps = {"opponents": [
        {"id": "o1", "commanders": ["Atraxa, Praetors' Voice"], "player": "Tim", "tags": ["combo", "nope"], "color_identity": ["W", "U", "B", "G"],
         "notes": [{"id": "n1", "text": "alt", "at": "2026-09-01T00:00:00+00:00"}, {"id": "n2", "text": "neu", "at": "2026-10-01T00:00:00+00:00"}]},
        {"id": "o2", "commanders": ["Krenko, Mob Boss"], "player": "Lisa", "tags": [], "color_identity": ["R"]},
    ]}  # fmt: skip
    return {"decks/meren.json": deck, "decks/.games/meren.json": games, "decks/.opponents.json": opps, "collection.json": []}


def test_snapshot_has_what_the_phone_shows():
    snap = appdata.snapshot(_docs())
    d = snap["decks"][0]
    assert d["slug"] == "meren" and d["colors"] == "BG" and d["level"] == "oberes Bracket 3"
    assert d["record"] == {"games": 2, "wins": 1, "losses": 1, "draws": 0, "last": "2026-10-03T20:00:00+00:00"}
    assert d["description"] == "Opfern und zurückholen."
    gc = next(r for r in d["rule0"]["rows"] if r["label"] == "Game Changer")
    assert gc["flag"] and "Demonic Tutor" in gc["value"] and "Rule 0" in d["rule0"]["text"]
    assert d["guide"]["key_cards"] == [{"name": "Sol Ring", "why": "Mana"}]  # no images from the PC
    assert d["cards"][1] == {"name": "Forest", "qty": 30, "category": "Land"}
    assert [g["id"] for g in snap["games"]] == ["g2", "g1"] and snap["games"][0]["deck"] == "meren"
    atraxa, krenko = sorted(snap["opponents"], key=lambda o: o["id"])
    assert atraxa["record"]["wins"] == 1 and atraxa["tags"] == ["combo"] and atraxa["colors"] == "WUBG"
    assert [n["text"] for n in atraxa["notes"]] == ["neu", "alt"]
    assert krenko["record"]["losses"] == 1  # an unlinked game counts by commander name
    assert snap["players"] == ["Lisa", "Tim"]
    assert ["combo", "Combo"] in snap["options"]["tags"] and ["commander", "Commander-Schaden"] in snap["options"]["how"]


def test_snapshot_of_nothing():
    snap = appdata.snapshot({})
    assert snap["decks"] == snap["games"] == snap["opponents"] == [] and snap["options"]["issues"]


def test_server_serves_the_app_with_a_strict_policy(tmp_path):
    client = TestClient(create_app(tmp_path))
    r = client.get("/app/")
    assert r.status_code == 200 and "Am Tisch" in r.text and "/app/js/main.js" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "api.scryfall.com" in csp
    assert client.get("/app", follow_redirects=False).headers["location"] == "/app/"
    for path in ("/app/js/main.js", "/app/app.css", "/app/manifest.webmanifest", "/app/demo.json", "/app/icons/icon-192.png"):
        assert client.get(path).status_code == 200, path
    assert "content-security-policy" not in client.get("/api/health").headers
