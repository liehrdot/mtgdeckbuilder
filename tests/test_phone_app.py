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


# ---- operations of the phone app (sync/appops.py) ----------------------------------------------

import json
import subprocess
import sys

import pytest

from mtgdeck.sync import appops

AT = "2026-10-10T20:00:00+00:00"


def _op(type_, payload, op_id="op000001"):
    return {"id": op_id, "type": type_, "payload": payload, "at": AT}


def test_game_add_links_known_and_new_opponents_like_the_desktop_app():
    docs = {"decks/meren.json": _docs()["decks/meren.json"], "decks/.games/meren.json": None,
            "decks/.opponents.json": _docs()["decks/.opponents.json"]}  # fmt: skip
    game = {"id": "g9", "deck": "meren", "result": "loss", "turn": 7, "issues": ["combo", "bogus"], "how": "combo", "started": 1,
            "mvp": "Sol Ring", "note": "knapp"}  # fmt: skip
    slots = [{"id": "o1", "commander": "Atraxa, Praetors' Voice", "note": "Thoracle"},
             {"commander": "Korvold, Fae-Cursed King", "new_id": "newkorv", "colors": "BRG"}]  # fmt: skip
    out = appops.apply_copy(_op("game.add", {"game": game, "opponents": slots}), docs)
    entry = out["decks/.games/meren.json"][-1]
    assert entry == {"id": "g9", "played": AT, "result": "loss", "opponents": ["Atraxa, Praetors' Voice", "Korvold, Fae-Cursed King"],
                     "turn": 7, "issues": ["combo"], "mvp": "Sol Ring", "note": "knapp", "version": 2,
                     "opponent_ids": ["o1", "newkorv"], "how": "combo", "started": 1}  # fmt: skip
    opps = {o["id"]: o for o in out["decks/.opponents.json"]["opponents"]}
    assert opps["newkorv"]["commanders"] == ["Korvold, Fae-Cursed King"] and opps["newkorv"]["color_identity"] == ["B", "R", "G"]
    assert opps["o1"]["notes"][-1]["text"] == "Thoracle" and opps["o1"]["notes"][-1]["game_id"] == "g9"
    # the same game again changes nothing
    docs2 = {**docs, **out}
    assert appops.apply_copy(_op("game.add", {"game": game, "opponents": slots}, "op000002"), docs2) == {}


def test_game_add_matches_an_opponent_by_commander_and_rejects_bad_input():
    docs = {"decks/meren.json": _docs()["decks/meren.json"], "decks/.games/meren.json": [], "decks/.opponents.json": _docs()["decks/.opponents.json"]}
    out = appops.apply_copy(_op("game.add", {"game": {"id": "g1", "deck": "meren", "result": "win"}, "opponents": [{"commander": "krenko, mob boss"}]}), docs)
    assert out["decks/.games/meren.json"][0]["opponent_ids"] == ["o2"]
    with pytest.raises(appops.OpError, match="Ergebnis"):
        appops.apply_copy(_op("game.add", {"game": {"id": "g2", "deck": "meren", "result": "?"}}), docs)
    with pytest.raises(appops.OpError, match="gibt es nicht mehr"):
        appops.apply_copy(_op("game.add", {"game": {"id": "g2", "deck": "gone", "result": "win"}}), {**docs, "decks/gone.json": None})
    with pytest.raises(appops.OpError, match="Unbekanntes Deck"):
        appops.paths(_op("game.add", {"game": {"id": "g2", "deck": "../x", "result": "win"}}))
    with pytest.raises(appops.OpError, match="Unbekannte Aktion"):
        appops.paths(_op("deck.delete", {}))


def test_opponent_operations():
    docs = {"decks/.opponents.json": None}
    out = appops.apply_copy(_op("opponent.add", {"opponent": {"id": "neu1", "commanders": ["Muldrotha, the Gravetide"], "colors": "UBG",
                                                               "player": "Jonas", "tags": ["graveyard", "x"]}, "note": "mag Friedhof"}), docs)  # fmt: skip
    o = out["decks/.opponents.json"]["opponents"][0]
    assert o["player"] == "Jonas" and o["tags"] == ["graveyard"] and o["notes"][0]["text"] == "mag Friedhof" and o["bracket"] is None
    docs = out
    docs = {**docs, **appops.apply_copy(_op("opponent.update", {"id": "neu1", "tags": ["combo"], "player": "Jo"}), docs)}
    docs = {**docs, **appops.apply_copy(_op("opponent.note", {"id": "neu1", "note_id": "n77", "text": "Thoracle"}), docs)}
    o = docs["decks/.opponents.json"]["opponents"][0]
    assert o["tags"] == ["combo"] and o["player"] == "Jo" and [n["id"] for n in o["notes"]][-1] == "n77"
    assert appops.apply_copy(_op("opponent.note", {"id": "neu1", "note_id": "n77", "text": "doppelt"}), docs) == {}
    docs = {**docs, **appops.apply_copy(_op("opponent.note_delete", {"id": "neu1", "note_id": "n77"}), docs)}
    assert all(n["id"] != "n77" for n in docs["decks/.opponents.json"]["opponents"][0]["notes"])
    docs = {**docs, **appops.apply_copy(_op("opponent.delete", {"id": "neu1"}), docs)}
    assert docs["decks/.opponents.json"]["opponents"] == []
    assert appops.apply_copy(_op("opponent.delete", {"id": "neu1"}), docs) == {}  # gone already


# ---- the routes: data, status, operations; a phone game reaches the PC ------------------------

def test_phone_reads_data_sends_operations_and_the_pc_gets_them(tmp_path, monkeypatch):
    from mtgdeck import games as games_mod, opponents as opponents_mod, storage
    from mtgdeck.jsonstore import write_json
    from mtgdeck.sync import Roots, service
    from mtgdeck.sync.remote import HttpTransport

    app = create_app(tmp_path / "server")
    client = TestClient(app)
    pc = Roots.current()
    service.connect(f"http://testserver/koppeln#{app.state.devices.new_code()['code']}", name="PC", roots=pc, client=client)
    storage.save({"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3, "cards": [{"name": "Sol Ring", "qty": 1, "category": "Ramp"}]})
    write_json(storage.DECKS_DIR / ".opponents.json", {"opponents": [{"id": "o1", "commanders": ["Atraxa, Praetors' Voice"], "player": "Tim", "tags": [], "notes": []}]})
    assert service.run(roots=pc, client=client)["ok"]

    phone = HttpTransport("http://testserver", client=client).claim(app.state.devices.new_code()["code"], "Handy", "phone")
    auth = {"Authorization": f"Bearer {phone['token']}"}
    assert client.get("/api/app/data").status_code == 401
    r = client.get("/api/app/data", headers=auth)
    data, etag = r.json(), r.headers["etag"]
    assert [d["slug"] for d in data["decks"]] == ["meren"] and data["opponents"][0]["player"] == "Tim"
    assert client.get("/api/app/data", headers={**auth, "If-None-Match": etag}).status_code == 304

    ops = [_op("game.add", {"game": {"id": "gphone1", "deck": "meren", "result": "win", "turn": 9, "how": "combat"},
                            "opponents": [{"id": "o1", "commander": "Atraxa, Praetors' Voice"}, {"commander": "Krenko, Mob Boss", "new_id": "krenko1", "colors": "R"}]}, "opphone01"),
           _op("game.add", {"game": {"id": "gphone2", "deck": "gone", "result": "win"}}, "opphone02"),
           _op("opponent.update", ["kaputt"], "opphone03")]  # fmt: skip
    res = client.post("/api/app/ops", json={"ops": ops}, headers=auth).json()
    assert res["results"][0] == {"id": "opphone01", "ok": True}
    assert res["results"][1]["ok"] is False and "gibt es nicht mehr" in res["results"][1]["error"]
    assert res["results"][2] == {"id": "opphone03", "ok": False, "error": "Ungültige Daten"}  # fails alone, never blocks the queue
    assert res["etag"] != etag
    again = client.post("/api/app/ops", json={"ops": ops[:1]}, headers=auth).json()  # answer was lost: sent again
    assert again["results"][0]["ok"]
    data = client.get("/api/app/data", headers=auth).json()
    assert [g["id"] for g in data["games"]] == ["gphone1"] and data["decks"][0]["record"]["wins"] == 1

    status = client.get("/api/app/status", headers=auth).json()
    assert status["pcs"][0]["name"] == "PC" and status["device"]["name"] == "Handy"

    run = service.run(roots=pc, client=client)  # the PC syncs: the game is there as if typed in at the PC
    assert run["ok"] and "Partien mit „Meren“" in run["incoming"]
    entry = games_mod.games("meren")[0]
    assert entry["result"] == "win" and entry["how"] == "combat" and entry["opponent_ids"] == ["o1", "krenko1"]
    assert games_mod.summary("meren")["stats"]["wins"] == 1
    krenko = opponents_mod.get("krenko1")
    assert krenko["commanders"] == ["Krenko, Mob Boss"] and opponents_mod.record(krenko)["games"] == 1


def test_pairing_link_and_root_lead_to_the_app_and_the_worker_is_versioned(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/koppeln", follow_redirects=False).headers["location"] == "/app/"
    assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
    sw = client.get("/app/sw.js")
    assert sw.status_code == 200 and "__VERSION__" not in sw.text and sw.headers["cache-control"] == "no-cache"
    assert "worker-src 'self' blob:" in client.get("/app/").headers["content-security-policy"]


def test_the_server_image_needs_no_card_libraries(tmp_path):
    """The Docker image installs the package without its app dependencies (httpx, Pillow, mcp)."""
    code = f"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('httpx', 'PIL', 'mcp', 'claude_agent_sdk'):
            raise ImportError('blocked ' + name)
sys.meta_path.insert(0, Block())
from mtgdeck.sync.server import create_app, app_snapshot, run_op
from fastapi.testclient import TestClient
app = create_app({str(tmp_path)!r})
print(app_snapshot(app.state.store)['decks'], TestClient(app).get('/app/sw.js').status_code)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[] 200"
