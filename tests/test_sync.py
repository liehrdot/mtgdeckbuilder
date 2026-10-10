"""Sync core: two devices (two data folders) and one server store – merges, decks, deletions, races."""

import json

import pytest

from mtgdeck import blacklist, collection, deskmat, proxy, storage, tablerules
from mtgdeck.jsonstore import write_json
from mtgdeck.sync import LocalTransport, Roots, SyncClient, SyncConflict, SyncStore
from mtgdeck.sync import store as store_mod
from mtgdeck.sync.files import kind_of


@pytest.fixture
def world(tmp_path, monkeypatch):
    store = SyncStore(tmp_path / "server" / "sync.sqlite")
    a, b = Roots.under(tmp_path / "a"), Roots.under(tmp_path / "b")

    def use(roots):  # the app's module globals point at this device
        monkeypatch.setattr(storage, "DECKS_DIR", roots.decks)
        monkeypatch.setattr(collection, "COLLECTION_FILE", roots.collection)
        monkeypatch.setattr(blacklist, "BLACKLIST_FILE", roots.blacklist)
        monkeypatch.setattr(tablerules, "TABLERULES_FILE", roots.tablerules)
        monkeypatch.setattr(proxy, "PROXIES_DIR", roots.proxies)
        monkeypatch.setattr(deskmat, "DESKMAT_DIR", roots.deskmats)
        return roots

    def sync(roots):
        return SyncClient(roots, LocalTransport(store)).sync()

    return store, a, b, use, sync


def _deck(name, cards, bracket=2):
    return {"name": name, "commanders": ["Meren of Clan Nel Toth"], "bracket": bracket,
            "cards": [{"name": n, "qty": q, "category": "Synergy"} for n, q in cards]}  # fmt: skip


def _read(roots, rel):
    return json.loads(roots.physical(rel).read_text("utf-8"))


def test_paths_that_sync_and_paths_that_do_not():
    assert kind_of("decks/meren.json") == "deck"
    assert kind_of("decks/.versions/meren/v0003.json") == "snapshot"
    assert kind_of("decks/.games/meren.json") == kind_of("collection.json") == kind_of("proxies/.orders/abc.json") == "json"
    assert kind_of("blacklist.txt") == "lines"
    assert kind_of("proxies/meren/uploads/0123456789abcdef.jpg") == "blob"
    for nope in ("decks/meren.txt", "decks/.trash/x/meren.json", "decks/.meren.json.lock", "mtgdeck.settings.json",
                 "proxies/meren/images/Sol Ring.jpg", "proxies/meren/prepared.json", "decks/../x.json",
                 "decks/meren.json.beschaedigt-1234", "deskmats/x/deskmat-100x50.png"):  # fmt: skip
        assert kind_of(nope) is None, nope


def test_first_sync_copies_everything_to_the_second_device(world):
    store, a, b, use, sync = world
    use(a)
    storage.save(_deck("Meren", [("Sol Ring", 1), ("Forest", 30)]))
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}])
    write_json(a.collection, [{"id": "c1", "name": "Sol Ring", "qty": 1}])
    a.blacklist.write_text("Cyclonic Rift\n", "utf-8")
    a.physical("proxies/meren/uploads/0123456789abcdef.jpg").parent.mkdir(parents=True)
    a.physical("proxies/meren/uploads/0123456789abcdef.jpg").write_bytes(b"\xff\xd8bild")
    (a.decks / ".trash").mkdir()
    (a.decks / ".trash" / "old.json").write_text("{}")
    r = sync(a)
    assert "decks/meren.json" in r["pushed"] and not r["errors"]

    r = sync(b)
    assert _read(b, "decks/meren.json")["cards"][0]["name"] == "Sol Ring"
    assert (b.decks / "meren.txt").read_text().startswith("1 Meren of Clan Nel Toth *CMDR*")  # export re-created
    assert _read(b, "decks/.versions/meren/v0001.json")["version"] == 1
    assert _read(b, "decks/.games/meren.json") == [{"id": "g1", "result": "win"}]
    assert b.blacklist.read_text() == "Cyclonic Rift\n"
    assert b.physical("proxies/meren/uploads/0123456789abcdef.jpg").read_bytes() == b"\xff\xd8bild"
    assert not (b.decks / ".trash").exists()
    assert sync(b)["pulled"] == [] and sync(a)["pushed"] == []  # nothing left to do


def test_games_from_two_devices_both_survive(world):
    store, a, b, use, sync = world
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}])
    sync(a), sync(b)
    write_json(a.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win"}, {"id": "g2", "result": "loss"}])
    write_json(b.physical("decks/.games/meren.json"), [{"id": "g1", "result": "win", "note": "Zug 7"}, {"id": "g3", "result": "win"}])
    sync(a)
    r = sync(b)
    assert "decks/.games/meren.json" in r["merged"] and r["conflicts"] == []
    sync(a)
    for roots in (a, b):
        games = _read(roots, "decks/.games/meren.json")
        assert [g["id"] for g in games] == ["g1", "g2", "g3"] and games[0]["note"] == "Zug 7"


def test_same_field_changed_on_both_devices_keeps_the_server_value_and_logs_it(world):
    store, a, b, use, sync = world
    opp = a.physical("decks/.opponents.json")
    write_json(opp, [{"id": "o1", "label": "Tims Atraxa", "notes": []}])
    sync(a), sync(b)
    write_json(opp, [{"id": "o1", "label": "Tim (Atraxa)", "notes": [{"id": "n1", "text": "Combo"}]}])
    write_json(b.physical("decks/.opponents.json"), [{"id": "o1", "label": "Atraxa von Tim", "notes": [{"id": "n2", "text": "schnell"}]}])
    sync(a)
    r = sync(b)
    merged = _read(b, "decks/.opponents.json")[0]
    assert merged["label"] == "Tim (Atraxa)" and [n["id"] for n in merged["notes"]] == ["n1", "n2"]
    assert r["conflicts"][0]["where"] == "[o1].label" and r["conflicts"][0]["local"] == "Atraxa von Tim"
    assert json.loads((b.state / "conflicts.json").read_text())[0]["kept"] == "Tim (Atraxa)"


def test_deleted_here_changed_there_is_kept(world):
    store, a, b, use, sync = world
    games = [{"id": "g1", "result": "win"}, {"id": "g2", "result": "loss"}]
    write_json(a.physical("decks/.games/meren.json"), games)
    sync(a), sync(b)
    write_json(a.physical("decks/.games/meren.json"), [games[1]])  # A deletes g1 …
    write_json(b.physical("decks/.games/meren.json"), [{**games[0], "note": "wichtig"}, games[1]])  # … B edits it
    sync(a)
    sync(b)
    assert [g["id"] for g in _read(b, "decks/.games/meren.json")] == ["g2", "g1"]
    # a plain deletion on one side (no edit on the other) wins
    write_json(b.physical("decks/.games/meren.json"), [games[1]])
    sync(b), sync(a)
    assert [g["id"] for g in _read(a, "decks/.games/meren.json")] == ["g2"]


def test_blacklist_lines_merge_as_a_set(world):
    store, a, b, use, sync = world
    a.blacklist.parent.mkdir(parents=True, exist_ok=True)
    a.blacklist.write_text("Cyclonic Rift\n@cheap-tutors\n", "utf-8")
    sync(a), sync(b)
    a.blacklist.write_text("Cyclonic Rift\n@cheap-tutors\nSol Ring\n", "utf-8")
    b.blacklist.write_text("@cheap-tutors\nMana Crypt\n", "utf-8")
    sync(a), sync(b), sync(a)
    assert a.blacklist.read_text() == b.blacklist.read_text() == "@cheap-tutors\nSol Ring\nMana Crypt\n"


def test_deck_changed_on_both_devices_merges_cards_and_keeps_all_versions(world):
    store, a, b, use, sync = world
    use(a)
    storage.save(_deck("Meren", [("Sol Ring", 1), ("Forest", 30)]))
    sync(a), sync(b)
    d = storage.load("meren")
    d["cards"].append({"name": "Viscera Seer", "qty": 1, "category": "Synergy"})
    storage.save(d)  # A: v2
    use(b)
    d = storage.load("meren")
    d["cards"].append({"name": "Pitiless Plunderer", "qty": 1, "category": "Synergy"})
    d["bracket"] = 3
    storage.save(d)  # B: also v2
    use(a), sync(a)
    use(b)
    r = sync(b)
    assert "decks/meren.json" in r["merged"] and r["revalidate"] == ["meren"]
    deck = storage.load("meren")
    assert {c["name"] for c in deck["cards"]} == {"Sol Ring", "Forest", "Viscera Seer", "Pitiless Plunderer"}
    assert deck["bracket"] == 3 and deck["version"] == 4 and deck["needs_revalidation"]
    assert [h["version"] for h in deck["history"]] == [1, 2, 3, 4]
    assert "auf diesem Gerät als v2 gespeichert" in deck["history"][2]["note"]
    assert deck["history"][3]["note"].startswith("Zusammengeführt")
    assert storage.load_version("meren", 2)["cards"][-1]["name"] == "Viscera Seer"  # A's v2
    assert storage.load_version("meren", 3)["cards"][-1]["name"] == "Pitiless Plunderer"  # B's former v2
    assert storage.load_version("meren", 3)["version"] == 3
    use(a), sync(a)
    deck_a = storage.load("meren")
    assert deck_a["version"] == 4 and {c["name"] for c in deck_a["cards"]} == {c["name"] for c in deck["cards"]}
    assert storage.load_version("meren", 4)["bracket"] == 3
    assert sync(a)["pushed"] == [] and sync(b)["pulled"] == []


def test_deck_only_extras_changed_on_one_side_needs_no_new_version(world):
    store, a, b, use, sync = world
    use(a)
    storage.save(_deck("Meren", [("Sol Ring", 1)]))
    sync(a), sync(b)
    storage.set_extra("meren", "guide", {"plan": "Opfern"})  # A: no new version
    use(b)
    d = storage.load("meren")
    d["cards"].append({"name": "Viscera Seer", "qty": 1})
    storage.save(d)  # B: v2
    use(a), sync(a)
    use(b), sync(b)
    deck = storage.load("meren")
    assert deck["version"] == 2 and deck["guide"] == {"plan": "Opfern"} and "needs_revalidation" not in deck
    assert [h["version"] for h in deck["history"]] == [1, 2]


def test_deck_deleted_on_one_device(world):
    store, a, b, use, sync = world
    use(a)
    storage.save(_deck("Meren", [("Sol Ring", 1)]))
    storage.save(_deck("Krenko", [("Sol Ring", 1)]))
    sync(a), sync(b)
    storage.delete("meren")  # A deletes, B did not touch it
    use(b)
    d = storage.load("krenko")
    d["bracket"] = 4
    storage.save(d)  # B changes krenko …
    use(a)
    storage.delete("krenko")  # … which A deletes
    sync(a)
    use(b)
    sync(b)
    assert not (b.decks / "meren.json").exists()
    assert list((b.decks / ".trash").glob("*-meren-sync/meren.json"))  # local copy kept in the trash
    assert storage.load("krenko")["bracket"] == 4  # changed here: stays
    assert storage.load_version("krenko", 1)["bracket"] == 2  # with its old versions
    use(a), sync(a)
    assert storage.load("krenko")["bracket"] == 4  # and comes back on A
    assert storage.load_version("krenko", 1)["bracket"] == 2


def test_same_deck_name_created_on_two_devices_keeps_both(world):
    store, a, b, use, sync = world
    use(a)
    storage.save(_deck("Meren", [("Sol Ring", 1)]))
    use(b)
    storage.save(_deck("Meren", [("Viscera Seer", 1)]))
    use(a), sync(a)
    use(b)
    r = sync(b)
    assert "meren-2" in r["conflicts"][0]["note"]
    assert storage.load("meren")["cards"][0]["name"] == "Sol Ring"
    mine = storage.load("meren-2")
    assert mine["cards"][0]["name"] == "Viscera Seer" and mine["slug"] == "meren-2"
    assert storage.load_version("meren-2", 1)["cards"][0]["name"] == "Viscera Seer"
    use(a), sync(a)
    assert storage.load("meren-2")["cards"][0]["name"] == "Viscera Seer"


def test_a_file_changed_during_the_sync_is_not_overwritten(world):
    store, a, b, use, sync = world
    path = "decks/.games/meren.json"
    write_json(a.physical(path), [{"id": "g1"}])
    sync(a), sync(b)
    write_json(a.physical(path), [{"id": "g1"}, {"id": "g2"}])
    sync(a)

    class Racing(LocalTransport):  # B's app writes the file right while the sync pulls
        def changes(self, since):
            write_json(b.physical(path), [{"id": "g1"}, {"id": "g9"}])
            return super().changes(since)

    r = SyncClient(b, Racing(store)).sync()
    assert path in r["skipped"]
    assert {g["id"] for g in _read(b, path)} >= {"g1", "g9"}  # the app's write survived
    sync(b)
    sync(a)
    assert {g["id"] for g in _read(a, path)} == {g["id"] for g in _read(b, path)} == {"g1", "g2", "g9"}


def test_store_conflicts_tombstones_and_paging(tmp_path, monkeypatch):
    s = SyncStore(tmp_path / "s.sqlite")
    r1 = s.put("collection.json", kind="json", data=b"[]", base_seq=None)
    assert s.put("collection.json", kind="json", data=b"[]", base_seq=None)["unchanged"]  # same content: no-op
    with pytest.raises(SyncConflict) as exc:
        s.put("collection.json", kind="json", data=b"[1]", base_seq=None)
    assert exc.value.current["seq"] == r1["seq"]
    r2 = s.put("collection.json", kind="json", data=None, base_seq=r1["seq"], deleted=True)
    assert s.get("collection.json")["deleted"] and r2["seq"] > r1["seq"]
    with pytest.raises(ValueError):
        s.put("decks/x.txt", kind="json", data=b"{}", base_seq=None)
    for i in range(5):
        s.put(f"decks/.games/d{i}.json", kind="json", data=b"[]", base_seq=None)
    page = s.changes(0, limit=2)
    assert len(page["docs"]) == 2 and page["more"]
    monkeypatch.setattr(store_mod, "PAGE", 2)
    calls = []

    class Counting(LocalTransport):
        def changes(self, since):
            calls.append(since)
            return super().changes(since)

    r = SyncClient(Roots.under(tmp_path / "c"), Counting(s)).sync()
    assert len(r["pulled"]) == 5 and calls[:3] == [0, 3, 5]  # 6 documents (5 games + a tombstone) in pages of 2
    assert sorted(p.name for p in (tmp_path / "c" / "decks" / ".games").glob("d*.json")) == [f"d{i}.json" for i in range(5)]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_three_devices_editing_at_random_converge(tmp_path, monkeypatch, seed):
    import random

    rnd = random.Random(seed)
    store = SyncStore(tmp_path / "server.sqlite")
    devices = [Roots.under(tmp_path / f"d{i}") for i in range(3)]

    def use(roots):
        monkeypatch.setattr(storage, "DECKS_DIR", roots.decks)

    def sync(roots):
        use(roots)
        r = SyncClient(roots, LocalTransport(store)).sync()
        assert not r["errors"], r["errors"]
        return r

    use(devices[0])
    storage.save(_deck("Meren", [("Sol Ring", 1)]))
    write_json(devices[0].physical("decks/.games/meren.json"), [{"id": "g0", "result": "win"}])
    write_json(devices[0].collection, [{"id": "c0", "name": "Sol Ring", "qty": 1}])
    for d in devices:
        sync(d)
    added, n = set(), 0
    for _ in range(80):
        d = rnd.choice(devices)
        op = rnd.choice(["game", "game", "edit", "coll", "qty", "deck", "sync", "sync"])
        use(d)
        if op == "game":
            n += 1
            games = _read(d, "decks/.games/meren.json")
            games.append({"id": f"g{seed}-{n}", "result": rnd.choice(["win", "loss"])})
            added.add(f"g{seed}-{n}")
            write_json(d.physical("decks/.games/meren.json"), games)
        elif op == "edit":
            games = _read(d, "decks/.games/meren.json")
            rnd.choice(games)["note"] = f"n{rnd.randint(0, 99)}"
            write_json(d.physical("decks/.games/meren.json"), games)
        elif op == "coll":
            n += 1
            write_json(d.collection, _read(d, "collection.json") + [{"id": f"c{seed}-{n}", "name": f"Filler {n}", "qty": 1}])
        elif op == "qty":
            items = _read(d, "collection.json")
            rnd.choice(items)["qty"] = rnd.randint(1, 4)
            write_json(d.collection, items)
        elif op == "deck":
            n += 1
            deck = storage.load("meren")
            deck["cards"].append({"name": f"Filler {n}", "qty": 1})
            storage.save(deck)
        else:
            sync(d)
    for _ in range(3):
        for d in devices:
            sync(d)
    snapshot = lambda d: (_read(d, "decks/.games/meren.json"), _read(d, "collection.json"), _read(d, "decks/meren.json"))  # noqa: E731
    first = snapshot(devices[0])
    for d in devices[1:]:
        assert snapshot(d) == first
    assert added <= {g["id"] for g in first[0]}  # no game got lost
    deck = first[2]
    for d in devices:  # every version in the history has its snapshot on every device
        use(d)
        for h in deck["history"]:
            assert storage.load_version("meren", h["version"])["version"] == h["version"]
    assert [h["version"] for h in deck["history"]] == list(range(1, deck["version"] + 1))
