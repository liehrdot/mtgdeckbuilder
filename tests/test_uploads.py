"""Own card images in the print studio: upload, make print-ready, choose, print, delete, back up."""

import io
import json
import zipfile

from conftest import deck_lines
from fastapi.testclient import TestClient
from PIL import Image

from mtgdeck import backup, imaging, proxy, storage
from mtgdeck.gui.app import app
from mtgdeck.mcp_server import mcp


async def _deck():
    await mcp.call_tool("save_deck", {"name": "Eigene Bilder", "commanders": ["Meren of Clan Nel Toth"], "bracket": 2,
        "cards": [{"name": l.split(" ", 1)[1], "qty": int(l.split(" ", 1)[0])} for l in deck_lines()]})  # fmt: skip
    return storage.load("eigene-bilder")


def _png(w, h, colour=(200, 30, 30), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, fmt)
    return buf.getvalue()


def test_card_from_upload_detects_bleed_crops_and_picks_dpi():
    # MPC template with bleed (2.74 x 3.74 in) at 300 DPI -> kept, just sized
    out, info = imaging.card_from_upload(Image.new("RGB", (822, 1122)))
    assert info["had_bleed"] and not info["cropped"] and out.size == imaging.FULL_PX and info["dpi"] == 300
    # card without bleed (63 x 88) -> bleed added
    out, info = imaging.card_from_upload(Image.new("RGB", (745, 1040)))
    assert not info["had_bleed"] and out.size == imaging.FULL_PX
    # big scan -> 600 DPI
    out, info = imaging.card_from_upload(Image.new("RGB", (1500, 2100)))
    assert info["dpi"] == 600 and out.size == imaging.sizes(600)[2]
    # square artwork -> cropped to card shape, low resolution flagged
    out, info = imaging.card_from_upload(Image.new("RGB", (400, 400)))
    assert info["cropped"] and info["low_res"] and out.size == imaging.FULL_PX
    # forced: the user says the image has no bleed although its ratio looks like it
    _, info = imaging.card_from_upload(Image.new("RGB", (822, 1122)), "no")
    assert not info["had_bleed"]


async def test_upload_route_chooses_and_prepare_prints_it():
    await _deck()
    with TestClient(app) as client:
        r = client.post("/api/decks/eigene-bilder/print/upload?face=Sol%20Ring&filename=mein-sol-ring.png",
                        content=_png(745, 1040), headers={"Content-Type": "image/png"})  # fmt: skip
        assert r.status_code == 200, r.text
        opt = r.json()
        assert opt["origin"] == "local" and opt["label"] == "Eigenes Bild: mein-sol-ring.png"
        assert "Beschnittrand wurde ergänzt." in opt["notes"]
        assert client.get(opt["thumb"]).headers["content-type"] == "image/jpeg"

        # it is chosen and listed first among the alternatives
        assert proxy.load_selection("eigene-bilder")["Sol Ring"]["upload"] == opt["upload"]
        plan = client.get("/api/decks/eigene-bilder/print/plan").json()
        sol = next(c for c in plan["cards"] if c["name"] == "Sol Ring")
        assert sol["front"]["image"]["origin"] == "local" and sol["front"]["image"]["custom"]
        alts = client.get("/api/decks/eigene-bilder/print/alternatives?card=Sol%20Ring").json()
        assert alts["options"][0]["upload"] == opt["upload"]

    deck = storage.load("eigene-bilder")
    await proxy.prepare(deck)
    prepared = json.loads((proxy.order_dir("eigene-bilder") / "prepared.json").read_text("utf-8"))
    face = prepared["faces"]["Sol Ring"]
    assert face["origin"] == "local" and face["original"].endswith("-original.png")
    with Image.open(face["file"]) as im:
        assert im.size == imaging.FULL_PX
    xml = (proxy.order_dir("eigene-bilder") / "eigene-bilder.xml").read_text("utf-8")
    assert "Sol Ring" in xml


async def test_upload_rejects_bad_files_and_delete_resets_choice():
    await _deck()
    with TestClient(app) as client:
        bad = client.post("/api/decks/eigene-bilder/print/upload?face=Sol%20Ring", content=b"not an image")
        assert bad.status_code == 400 and "kein lesbares Bild" in bad.json()["detail"]
        assert client.post("/api/decks/eigene-bilder/print/upload?face=Sol%20Ring", content=b"").status_code == 400
        assert client.post("/api/decks/gibts-nicht/print/upload?face=X", content=_png(10, 10)).status_code == 404
        assert client.get("/api/decks/eigene-bilder/print/uploads/..%2F..%2Fsecret.jpg").status_code == 404

        opt = client.post("/api/decks/eigene-bilder/print/upload?face=Sol%20Ring", content=_png(400, 400, fmt="JPEG")).json()
        assert any("zugeschnitten" in n for n in opt["notes"]) and any("Niedrige Auflösung" in n for n in opt["notes"])
        assert client.delete(f"/api/decks/eigene-bilder/print/uploads/{opt['upload']}").json() == {"ok": True}
        assert "Sol Ring" not in proxy.load_selection("eigene-bilder")
        assert proxy.uploads("eigene-bilder") == []
        assert client.delete(f"/api/decks/eigene-bilder/print/uploads/{opt['upload']}").status_code == 404


async def test_uploads_are_backed_up_and_restored():
    await _deck()
    opt = proxy.save_upload("eigene-bilder", "Sol Ring", _png(745, 1040), "x.png")
    proxy.choose("eigene-bilder", "Sol Ring", opt)
    info = backup.create("manuell")
    with zipfile.ZipFile(backup.BACKUP_DIR / info["name"]) as z:
        names = z.namelist()
    assert f"proxies/eigene-bilder/uploads/{opt['upload']}.jpg" in names
    assert "proxies/eigene-bilder/uploads/index.json" in names
    for f in (proxy.order_dir("eigene-bilder") / "uploads").iterdir():
        f.unlink()
    backup.restore(info["name"])
    assert [u["upload"] for u in proxy.uploads("eigene-bilder", "Sol Ring")] == [opt["upload"]]
