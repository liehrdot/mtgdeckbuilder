"""Sync server over HTTP: pairing, devices, presence, the sync protocol and the device side (service)."""

import json

import pytest
from fastapi.testclient import TestClient

from mtgdeck import blacklist, collection, proxy, storage, tablerules
from mtgdeck.jsonstore import write_json
from mtgdeck.sync import Roots, service
from mtgdeck.sync.remote import AuthError, HttpTransport, RemoteError, normalize_url, parse_link
from mtgdeck.sync.server import Devices, create_app, daily_backup, main
from mtgdeck.sync.store import SyncConflict

URL = "http://testserver"


@pytest.fixture
def net(tmp_path, monkeypatch):
    app = create_app(tmp_path / "server", public_url="https://sync.example.de")
    client = TestClient(app)
    a, b = Roots.under(tmp_path / "a"), Roots.under(tmp_path / "b")

    def use(roots):
        monkeypatch.setattr(storage, "DECKS_DIR", roots.decks)
        monkeypatch.setattr(collection, "COLLECTION_FILE", roots.collection)
        monkeypatch.setattr(blacklist, "BLACKLIST_FILE", roots.blacklist)
        monkeypatch.setattr(tablerules, "TABLERULES_FILE", roots.tablerules)
        monkeypatch.setattr(proxy, "PROXIES_DIR", roots.proxies)
        return roots

    def code():
        return app.state.devices.new_code(by="console")["code"]

    def pair(roots, name):
        return service.connect(f"{URL}/koppeln#{code()}", name=name, roots=roots, client=client)

    return app, client, a, b, use, pair, code


def test_links_and_addresses():
    assert normalize_url("sync.example.de/") == "https://sync.example.de"
    assert normalize_url("https://sync.example.de/koppeln#AB") == "https://sync.example.de"
    assert parse_link("https://sync.example.de/koppeln#ABCD-EFGH") == ("https://sync.example.de", "ABCD-EFGH")
    assert parse_link("abcd-efgh") == ("", "abcd-efgh")
    assert parse_link("sync.example.de") == ("https://sync.example.de", "")


def test_pairing_codes_work_once_and_wrong_codes_are_limited(net):
    app, client, a, b, use, pair, code = net
    c = code()
    t = HttpTransport(URL, client=client)
    assert t.health()["service"] == "mtgdeck-sync"
    got = t.claim(c.lower().replace("-", ""), "PC")  # case and dash do not matter
    assert got["token"] and got["server"] == "https://sync.example.de"
    with pytest.raises(RemoteError, match="stimmt nicht oder ist abgelaufen"):
        t.claim(c, "Laptop")  # used
    for _ in range(19):
        with pytest.raises(RemoteError):
            t.claim("XXXX-XXXX", "Raten")
    with pytest.raises(RemoteError, match="Zu viele falsche Codes"):
        t.claim(code(), "Laptop")  # even a right code waits now
    stored = (app.state.devices.path).read_bytes()
    assert got["token"].encode() not in stored  # only the hash is stored


def test_two_pcs_sync_through_the_server(net):
    app, client, a, b, use, pair, code = net
    use(a)
    st = pair(a, "PC")
    assert "token" not in st and st["name"] == "PC"
    storage.save({"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
                  "cards": [{"name": "Sol Ring", "qty": 1, "category": "Ramp"}]})  # fmt: skip
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}])
    run = service.run("test", roots=a, client=client)
    assert run["ok"] and "decks/meren.json" in run["pushed"] and "Deck „Meren“" in run["outgoing"]

    use(b)
    pair(b, "Laptop")
    run = service.run("test", roots=b, client=client)
    assert run["ok"] and "Partien mit „Meren“" in run["incoming"] and "Deck „Meren“" in run["incoming"]
    assert json.loads(b.physical("decks/meren.json").read_text())["cards"][0]["name"] == "Sol Ring"
    write_json(b.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}, {"id": "g2", "result": "loss"}])
    assert service.run(roots=b, client=client)["pushed"] == ["decks/.games/meren.json"]

    use(a)
    assert service.run(roots=a, client=client)["incoming"] == ["Partien mit „Meren“"]
    assert [g["id"] for g in json.loads(a.physical("decks/.games/meren.json").read_text())] == ["g1", "g2"]
    status = service.status(a)
    assert status["connected"] and status["last"]["ok"] and status["last_ok"] and "token" not in json.dumps(status)


def test_conflicting_put_answers_409_with_the_current_document(net):
    app, client, a, b, use, pair, code = net
    use(a)
    pair(a, "PC")
    t = service.transport(service.config(a), client)
    first = t.put({"path": "collection.json", "kind": "json", "data": b"[]", "base_seq": None})
    with pytest.raises(SyncConflict) as err:
        t.put({"path": "collection.json", "kind": "json", "data": b"[1]", "base_seq": None})
    assert err.value.current["seq"] == first["seq"] and err.value.current["data"] == b"[]"
    r = client.put("/api/sync/doc", json={"path": "../x.json", "kind": "json", "data": ""},
                   headers={"Authorization": f"Bearer {service.config(a)['token']}"})  # fmt: skip
    assert r.status_code == 400
    assert client.get("/api/sync/changes").status_code == 401  # no token


def test_devices_presence_new_codes_and_signing_off(net):
    app, client, a, b, use, pair, code = net
    pair(a, "PC")
    pair(b, "Handy")
    assert service.presence({"ai": {"ready": True}}, roots=a, client=client)
    devs = {d["name"]: d for d in service.devices(roots=a, client=client)}
    assert devs["PC"]["this"] and devs["PC"]["online"] and devs["PC"]["info"]["ai"] == {"ready": True}
    assert not devs["Handy"]["this"]

    got = service.pair_code(roots=a, client=client)
    assert got["link"] == f"https://sync.example.de/koppeln#{got['code']}" and got["qr"].startswith("<svg")

    service.revoke(devs["Handy"]["id"], roots=a, client=client)  # the phone was lost
    run = service.run(roots=b, client=client)
    assert not run["ok"] and "nicht (mehr) angemeldet" in run["error"]
    assert service.status(b)["revoked"]
    assert not service.presence({}, roots=b, client=client)

    service.revoke(devs["PC"]["id"], roots=a, client=client)  # this device: signs off and forgets the token
    assert not service.status(a)["connected"]
    assert (a.state / "state.json").exists()  # the sync state stays for a later reconnect
    with pytest.raises(AuthError):
        HttpTransport(URL, "falsch", client=client).devices()


def test_a_reset_merges_afresh_without_duplicating_decks(net):
    app, client, a, b, use, pair, code = net
    use(a)
    pair(a, "PC")
    deck = {"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
            "cards": [{"name": "Sol Ring", "qty": 1, "category": "Ramp"}]}  # fmt: skip
    storage.save(dict(deck))
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}])
    service.run(roots=a, client=client)

    # a backup is restored: one game is missing, the deck got a new local version
    write_json(a.physical("decks/.games/meren.json"), [])
    storage.save({**deck, "cards": [*deck["cards"], {"name": "Cultivate", "qty": 1, "category": "Ramp"}]})
    service.reset(a)
    run = service.run(roots=a, client=client)
    assert run["ok"]
    assert sorted(p.name for p in a.decks.glob("*.json")) == ["meren.json"]  # no meren-2
    mine = json.loads(a.physical("decks/meren.json").read_text())
    assert [c["name"] for c in mine["cards"]] == ["Sol Ring", "Cultivate"] and mine["version"] == 2
    assert json.loads(a.physical("decks/.games/meren.json").read_text()) == [{"id": "g1", "result": "win"}]  # nothing lost


def test_another_server_at_the_same_address_is_noticed(net, tmp_path):
    app, client, a, b, use, pair, code = net
    use(a)
    pair(a, "PC")
    other = TestClient(create_app(tmp_path / "other"))
    run = service.run(roots=a, client=other)
    assert not run["ok"] and "anderer Sync-Server" in run["error"]


def test_server_backups_and_console_commands(net, tmp_path, capsys, monkeypatch):
    app, client, a, b, use, pair, code = net
    pair(a, "PC")
    folder = tmp_path / "server" / "backups"
    assert daily_backup(app.state.store, folder) is not None
    assert daily_backup(app.state.store, folder) is None  # once a day
    for day in range(1, 20):
        (folder / f"sync-202601{day:02d}.sqlite").write_bytes(b"")
    daily_backup(app.state.store, folder, keep=14)
    assert len(list(folder.glob("sync-*.sqlite"))) == 14

    monkeypatch.setenv("MTG_SYNC_DATA", str(tmp_path / "server"))
    monkeypatch.setenv("MTG_SYNC_PUBLIC_URL", "https://sync.example.de")
    main(["pair"])
    out = capsys.readouterr().out
    assert "Kopplungscode:" in out and "https://sync.example.de/koppeln#" in out
    main(["devices"])
    assert "PC" in capsys.readouterr().out
    assert Devices(tmp_path / "server" / "sync.sqlite").list()[0]["name"] == "PC"


def test_connect_explains_missing_and_wrong_input(net):
    app, client, a, b, use, pair, code = net
    with pytest.raises(ValueError, match="Adresse"):
        service.connect("ABCD-EFGH", roots=a, client=client)
    with pytest.raises(ValueError, match="Kopplungscode"):
        service.connect(URL, roots=a, client=client)
    with pytest.raises(RemoteError, match="stimmt nicht"):
        service.connect(url=URL, code="ABCD-EFGH", roots=a, client=client)
    assert not service.status(a)["connected"]


def test_gui_routes_connect_sync_pair_and_check_merged_decks(net, monkeypatch):
    from mtgdeck.gui import app as gui

    app, client, a, b, use, pair, code = net
    monkeypatch.setenv("MTG_SYNC_AUTO", "0")
    monkeypatch.setattr(service, "HttpTransport", lambda url, token="", client=None: HttpTransport(url, token, client=net[1]))
    with TestClient(gui.app) as g:
        assert g.get("/api/sync").json()["connected"] is False
        assert g.post("/api/sync/run").status_code == 400
        r = g.post("/api/sync/connect", json={"link": f"{URL}/koppeln#ABCD-EFGH", "name": "PC"})
        assert r.status_code == 400 and "stimmt nicht" in r.json()["detail"]
        r = g.post("/api/sync/connect", json={"link": f"{URL}/koppeln#{code()}", "name": "PC"})
        body = r.json()
        assert r.status_code == 200 and body["connected"] and body["run"]["ok"] and "token" not in json.dumps(body)

        storage.save({"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
                      "cards": [{"name": "Sol Ring", "qty": 1, "category": "Ramp"}]})  # fmt: skip
        deck = storage.load("meren")
        deck["needs_revalidation"] = True  # as a merge from two devices leaves it
        write_json(storage.DECKS_DIR / "meren.json", deck)
        assert g.get("/api/sync").json()["pending_revalidation"] == ["meren"]
        run = g.post("/api/sync/run").json()["run"]
        assert "decks/meren.json" in run["pushed"] and run["revalidated"] == ["meren"]
        fresh = storage.load("meren")
        assert "needs_revalidation" not in fresh and fresh["version"] == 1 and fresh["validation"]

        devs = g.get("/api/sync/devices").json()["devices"]
        assert [d["name"] for d in devs] == ["PC"] and devs[0]["this"]
        pairing = g.post("/api/sync/pair").json()
        assert pairing["qr"].startswith("<svg") and "#" in pairing["link"]

        before = gui._sync_state["dirty"]
        g.post("/api/settings", json={"sync_interval": 15})
        assert gui._sync_state["dirty"] > before and g.get("/api/sync").json()["interval"] == 15
        assert g.post("/api/settings", json={"sync_interval": -1}).status_code == 422

        assert g.get("/api/sync/conflicts").json() == []
        assert g.delete("/api/sync/conflicts").json() == {"ok": True}
        assert g.post("/api/sync/disconnect").json()["connected"] is False


def test_gui_status_after_a_run_shows_that_run(net, monkeypatch):
    from mtgdeck.gui import app as gui

    app, client, a, b, use, pair, code = net
    monkeypatch.setenv("MTG_SYNC_AUTO", "0")
    monkeypatch.setattr(service, "HttpTransport", lambda url, token="", client=None: HttpTransport(url, token, client=net[1]))
    with TestClient(gui.app) as g:
        body = g.post("/api/sync/connect", json={"link": f"{URL}/koppeln#{code()}", "name": "PC"}).json()
        assert body["last"]["at"] == body["run"]["at"]
        body = g.post("/api/sync/run").json()
        assert body["last"]["at"] == body["run"]["at"]


def test_restoring_a_server_backup_lets_devices_merge_afresh(net, tmp_path, monkeypatch, capsys):
    app, client, a, b, use, pair, code = net
    use(a)
    pair(a, "PC")
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}])
    service.run(roots=a, client=client)
    saved = app.state.store.backup(tmp_path / "server" / "backups" / "sync-20261001.sqlite")
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}, {"id": "g2", "result": "loss"}])
    service.run(roots=a, client=client)
    epoch = app.state.store.epoch()

    monkeypatch.setenv("MTG_SYNC_DATA", str(tmp_path / "server"))
    main(["restore", str(saved)])
    assert "Zurückgespielt" in capsys.readouterr().out
    assert app.state.store.epoch() != epoch
    assert len(list((tmp_path / "server" / "backups").glob("vor-wiederherstellung-*.sqlite"))) == 1
    assert json.loads(app.state.store.get("decks/.games/meren.json")["data"]) == [{"id": "g1", "result": "win"}]

    run = service.run(roots=a, client=client)  # the PC notices and brings back what the backup lacks
    assert run["ok"] and run.get("reset") and "decks/.games/meren.json" in run["pushed"]
    games = json.loads(app.state.store.get("decks/.games/meren.json")["data"])
    assert [g["id"] for g in games] == ["g1", "g2"]
    assert service.config(a)["epoch"] == app.state.store.epoch()
    assert not service.run(roots=a, client=client).get("reset")  # once


def test_restoring_an_app_backup_on_a_synced_pc_merges_instead_of_deleting(net):
    from mtgdeck import backup

    app, client, a, b, use, pair, code = net
    roots = Roots.current()
    service.connect(f"{URL}/koppeln#{code()}", name="PC", roots=roots, client=client)
    write_json(roots.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}])
    saved = backup.create("manuell")
    write_json(roots.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}, {"id": "g2", "result": "loss"}])
    service.run(roots=roots, client=client)  # g2 is on the server now

    backup.restore(saved["name"])  # the backup does not know g2
    assert json.loads((roots.state / "state.json").read_text()).get("cursor") is None  # merged afresh next time
    run = service.run(roots=roots, client=client)
    assert run["ok"]
    assert [g["id"] for g in json.loads(roots.physical("decks/.games/meren.json").read_text())] == ["g1", "g2"]


def test_run_lists_decks_first_and_counts_the_rest(net):
    app, client, a, b, use, pair, code = net
    use(a)
    pair(a, "PC")
    for i in range(15):
        write_json(a.physical(f"decks/.games/deck{i:02d}.json"), [{"id": f"g{i}"}])
    storage.save({"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2, "cards": []})
    run = service.run(roots=a, client=client)
    assert run["outgoing"][0] == "Deck „Meren“" and len(run["outgoing"]) == 12 and run["outgoing_more"] == 4
