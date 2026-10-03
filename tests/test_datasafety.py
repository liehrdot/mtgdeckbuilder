"""Data safety: atomic/locked writes, damaged files, edit conflicts, same-name decks, job→deck detection,
trash, backups and the GUI's host/origin guard."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import textwrap
import zipfile

import pytest
from conftest import deck_lines
from fastapi.testclient import TestClient

from mtgdeck import backup, collection, deckedit, jsonstore, mcp_server, storage, tablerules
from mtgdeck.gui import app as gui

MEREN = ["Meren of Clan Nel Toth"]


def _deck(name: str = "Safe Deck", **extra) -> str:
    storage.save({"name": name, "commanders": MEREN, "bracket": 3, "currency": "eur",
                  "cards": [{"name": ln.split(" ", 1)[1], "qty": int(ln.split(" ", 1)[0])} for ln in deck_lines()], **extra})  # fmt: skip
    return storage.slug(name)


def _cards():
    return [mcp_server.CardEntry(name=ln.split(" ", 1)[1], qty=int(ln.split(" ", 1)[0])) for ln in deck_lines()]


# --- jsonstore -----------------------------------------------------------------------------------


def test_atomic_write_and_damaged_file_is_kept(tmp_path):
    p = tmp_path / "x.json"
    jsonstore.write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]  # no temp files left
    assert jsonstore.read_json(tmp_path / "missing.json", []) == []
    p.write_text('{"a": 1, "b":')  # half-written
    with pytest.raises(jsonstore.StoreError, match="beschädigt"):
        jsonstore.read_json(p, {})
    copies = list(tmp_path.glob("x.json.beschaedigt-*"))
    assert len(copies) == 1 and copies[0].read_text() == '{"a": 1, "b":'
    with pytest.raises(jsonstore.StoreError):
        jsonstore.read_json(p, {})
    assert len(list(tmp_path.glob("x.json.beschaedigt-*"))) == 1  # one copy per content, not per read


async def test_damaged_collection_is_never_overwritten():
    collection.COLLECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    collection.COLLECTION_FILE.write_text('[{"name": "Sol Ring", "qty": 1')
    with pytest.raises(jsonstore.StoreError):
        await collection.add([{"name": "Cultivate", "qty": 1}])
    assert collection.COLLECTION_FILE.read_text() == '[{"name": "Sol Ring", "qty": 1'


def test_lock_is_reentrant_and_works_across_processes(tmp_path):
    p = tmp_path / "data.json"
    with jsonstore.locked(p), jsonstore.locked(p):  # same thread: no deadlock
        pass
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        from mtgdeck import jsonstore
        with jsonstore.locked(__import__("pathlib").Path({str(p)!r})):
            print("locked", flush=True)
            time.sleep(3)
    """)], stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(jsonstore.StoreError, match="gesperrt"):
            with jsonstore.locked(p, timeout=0.3):
                pass
    finally:
        holder.wait(10)
    with jsonstore.locked(p, timeout=1):  # free again
        pass


# --- decks ---------------------------------------------------------------------------------------


def test_save_keeps_extras_and_detects_conflicts():
    slug = _deck(precon={"name": "Grave Danger"})
    storage.set_extra(slug, "guide", {"plan": "x"})
    storage.set_extra(slug, "upgrade_plan", {"stages": []})
    # a save built from scratch (like Claude's save_deck) keeps them
    _deck(change_note="Claude-Umbau")
    deck = storage.load(slug)
    assert deck["guide"] == {"plan": "x"} and deck["upgrade_plan"] == {"stages": []} and deck["precon"]["name"] == "Grave Danger"
    assert deck["version"] == 2
    stale = storage.load(slug)
    _deck(description="neuer", change_note="dazwischen")
    stale["description"] = "veraltet"
    with pytest.raises(jsonstore.ConflictError):
        storage.save(stale, expect_version=2)
    assert storage.load(slug)["description"] == "neuer"


def test_damaged_deck_is_listed_and_not_overwritten():
    slug = _deck()
    (storage.DECKS_DIR / f"{slug}.json").write_text("{kaputt")
    row = next(d for d in storage.list_decks() if d["slug"] == slug)
    assert row["damaged"] and "beschädigt" in row["name"]
    with pytest.raises(jsonstore.StoreError):
        _deck()
    assert (storage.DECKS_DIR / f"{slug}.json").read_text() == "{kaputt"
    with TestClient(gui.app) as client:
        r = client.get(f"/api/decks/{slug}")
    assert r.status_code == 503 and "beschädigt" in r.json()["detail"]


async def test_edit_survives_a_save_in_between(monkeypatch):
    slug = _deck()
    real = deckedit.revalidate
    calls = {"n": 0}

    async def revalidate_and_interfere(deck):
        calls["n"] += 1
        if calls["n"] == 1:  # a Claude job saves while the manual edit is validating
            other = storage.load(slug)
            other["description"] = "von Claude"
            other["change_note"] = "Claude"
            storage.save(other)
        return await real(deck)

    monkeypatch.setattr(deckedit, "revalidate", revalidate_and_interfere)
    out = await deckedit.edit_deck(slug, add=[{"name": "Demonic Tutor"}])
    deck = storage.load(slug)
    assert deck["description"] == "von Claude"  # Claude's change survived
    assert any(c["name"] == "Demonic Tutor" for c in deck["cards"])  # and the edit was applied on top
    assert out["version"] == 3 and calls["n"] == 2


async def test_new_deck_with_same_name_gets_own_slug(monkeypatch):
    first = await mcp_server.save_deck(name="Meren", commanders=MEREN, cards=_cards(), bracket=3)
    assert "slug='meren'" in first["hint"]
    # saving again right away with the same commander = the same build (validate, fix, save again)
    again = await mcp_server.save_deck(name="Meren", commanders=MEREN, cards=_cards(), bracket=2)
    assert again["slug"] == "meren" and storage.load("meren")["bracket"] == 2
    # an older deck with that name is never overwritten
    deck = storage.load("meren")
    deck["updated"] = "2020-01-01T00:00:00+00:00"
    storage._write_current(deck)
    out = await mcp_server.save_deck(name="Meren", commanders=MEREN, cards=_cards(), bracket=4)
    assert out["slug"] == "meren-2" and storage.load("meren")["bracket"] == 2
    # nor is a recent deck of another commander
    other = await mcp_server.save_deck(name="Meren", commanders=["Atraxa, Praetors' Voice"], cards=_cards(), bracket=3)
    assert other["slug"] == "meren-3"
    # the same job saving again (fix and save) keeps its deck
    monkeypatch.setenv("MTG_JOB_ID", "job123")
    first = await mcp_server.save_deck(name="Gruul", commanders=MEREN, cards=_cards(), bracket=3)
    again = await mcp_server.save_deck(name="Gruul", commanders=MEREN, cards=_cards(), bracket=2)
    assert first["slug"] == again["slug"] == "gruul" and storage.load("gruul")["last_job"] == "job123"
    monkeypatch.setenv("MTG_JOB_ID", "job999")
    other = await mcp_server.save_deck(name="Gruul", commanders=MEREN, cards=_cards(), bracket=3)
    assert other["slug"] == "gruul-2"


def test_job_finds_its_own_deck_not_one_saved_meanwhile(monkeypatch):
    import claude_agent_sdk

    job = gui.Job(id="jobabc")

    async def fake_query(prompt, options):
        assert options.mcp_servers["mtg"]["env"]["MTG_JOB_ID"] == "jobabc"
        monkeypatch.setenv("MTG_JOB_ID", "jobabc")  # what the job's MCP server does
        _deck("Built By Job")
        monkeypatch.delenv("MTG_JOB_ID")
        _deck("Edited Meanwhile")  # the user edits another deck during the build
        if False:
            yield None

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    asyncio.run(gui._run_claude(job, "x", None, extras={"built_against": ["o1"]}))
    done = job.events[-1]
    assert done["type"] == "done" and done["deck"] == "built-by-job"
    assert storage.load("built-by-job")["built_against"] == ["o1"]
    assert "built_against" not in storage.load("edited-meanwhile")


def test_trash_restore_and_purge():
    from mtgdeck import games

    slug = _deck()
    games.add(slug, result="win", opponents=["Krenko, Mob Boss"])
    storage.add_question(slug, "Wie?", "So.")
    tid = storage.delete(slug)
    assert all(d["slug"] != slug for d in storage.list_decks())
    assert storage.trash()[0]["id"] == tid and storage.trash()[0]["name"] == "Safe Deck"
    _deck()  # a new deck took the name meanwhile
    back = storage.restore_deleted(tid)
    assert back["slug"] == "safe-deck-2"
    assert len(games.games("safe-deck-2")) == 1 and storage.questions("safe-deck-2")[0]["answer"] == "So."
    assert storage.versions("safe-deck-2") and storage.trash() == []
    t2 = storage.delete("safe-deck")
    assert storage.purge_deleted(t2) == 1 and storage.trash() == []
    with pytest.raises(FileNotFoundError):
        storage.restore_deleted("../../etc")


def test_table_rule_is_deleted_only_after_its_decks(monkeypatch):
    rs = asyncio.run(tablerules.create("Freitag"))
    _deck(table_rule=rs["id"])

    async def boom(*a, **k):
        raise RuntimeError("Netzwerk weg")

    monkeypatch.setattr(gui, "_set_table_rule", boom)
    with TestClient(gui.app, raise_server_exceptions=False) as client:
        assert client.delete(f"/api/tablerules/{rs['id']}").status_code == 500
    assert tablerules.get(rs["id"])  # the rule still exists, no deck points to nothing


# --- backups -------------------------------------------------------------------------------------


async def test_backup_roundtrip_with_safety_copy():
    from mtgdeck import blacklist, opponents

    slug = _deck()
    await collection.add([{"name": "Sol Ring", "qty": 2}])
    await blacklist.update(add=["True Duals"])
    await opponents.create(["Krenko, Mob Boss"], note="schnell")
    b = backup.create("manuell")
    assert b["decks"] == 1 and b["valid"] and b["kind"] == "manuell"
    with zipfile.ZipFile(backup.BACKUP_DIR / b["name"]) as z:
        names = set(z.namelist())
    assert {"backup.json", f"decks/{slug}.json", "decks/.opponents.json", "collection.json", "blacklist.txt"} <= names
    assert not any(n.endswith(".lock") or n.endswith(".tmp") for n in names)

    storage.delete(slug)
    await collection.add([{"name": "Cultivate", "qty": 1}], replace=True)
    r = backup.restore(b["name"])
    assert r["decks"] == 1 and storage.load(slug)["name"] == "Safe Deck"
    assert [e["name"] for e in collection.load()] == ["Sol Ring"]
    assert opponents.all_opponents()[0]["notes"][0]["text"] == "schnell"
    safety = next(x for x in backup.backups() if x["kind"] == "vor-wiederherstellung")
    assert safety["name"] == r["safety_backup"]


def test_backup_upload_rejects_foreign_or_unsafe_zips():
    with pytest.raises(ValueError, match="keine Sicherung"):
        backup.upload(b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("backup.json", json.dumps({"app": "mtgdeckbuilder", "format": 1}))
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError):
        backup.upload(buf.getvalue())
    with pytest.raises(FileNotFoundError):
        backup.path_of("../../secret.zip")


def test_auto_backup_once_a_day_and_pruned():
    assert backup.auto_backup() is None  # nothing to back up yet
    _deck()
    assert backup.auto_backup()["kind"] == "auto"
    assert backup.auto_backup() is None  # not twice a day
    for _ in range(backup.KEEP_AUTO + 2):
        backup.create("auto")
    assert len([b for b in backup.backups() if b["kind"] == "auto"]) == backup.KEEP_AUTO


def test_backup_and_trash_routes():
    slug = _deck()
    with TestClient(gui.app) as client:
        b = client.post("/api/backups").json()
        listed = client.get("/api/backups").json()
        assert listed["backups"][0]["name"] == b["name"]
        r = client.get(f"/api/backups/{b['name']}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        up = client.post("/api/backups/upload", content=r.content).json()
        assert up["kind"] == "hochgeladen" and up["decks"] == 1
        assert client.post("/api/backups/upload", content=b"x").status_code == 400
        client.delete(f"/api/decks/{slug}")
        assert client.post(f"/api/backups/{up['name']}/restore").json()["decks"] == 1
        assert client.get(f"/api/decks/{slug}").status_code == 200
        assert client.post("/api/backups/nope.zip/restore").status_code == 404


# --- GUI guard -----------------------------------------------------------------------------------


def test_foreign_hosts_and_cross_site_posts_are_rejected():
    with TestClient(gui.app) as client:
        assert client.get("/api/decks").status_code == 200
        assert client.get("/api/decks", headers={"host": "evil.example:8765"}).status_code == 400
        r = client.post("/api/settings", json={"descreen": "light"}, headers={"origin": "https://evil.example"})
        assert r.status_code == 403
        ok = client.post("/api/settings", json={"descreen": "light"}, headers={"origin": "http://127.0.0.1:8765"})
        assert ok.status_code == 200
        assert client.get("/api/decks", headers={"host": "localhost:8765"}).status_code == 200
