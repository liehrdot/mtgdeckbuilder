"""Deskmat studio: sources, crop maths, rendering to ~4K with (fake) Real-ESRGAN, generator, routes."""

import asyncio
import io
import re
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_proxy import _fake_esrgan

from mtgdeck import deskmat, settings
from mtgdeck.gui import app as app_mod
from mtgdeck.gui.app import DeskmatGenerateRequest, app, deskmat_prompt
from mtgdeck.mcp_server import mcp


def _png(size=(900, 600), color=(40, 90, 160)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.paste((220, 180, 40), (size[0] // 2, 0, size[0], size[1] // 2))  # a corner to see crops
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def small_format(monkeypatch):
    """A tiny mat (60 x 35 mm -> 709 x 413 px at 300 DPI) keeps the rendering tests fast."""
    monkeypatch.setitem(deskmat.FORMATS, "test", ("Test 6 × 3,5 cm", 60, 35))
    return "test"


def test_formats_and_crop_maths():
    assert deskmat.target_size("playmat", 300) == (7205, 4193)
    assert deskmat.target_size("playmat", 600) == (14409, 8386)
    assert deskmat.target_size("playmat", 300, 3) == (7276, 4264)  # 3 mm bleed on every side
    assert deskmat.check_size("deskmat-90x40", 600, 0) == (21260, 9449)
    with pytest.raises(ValueError, match="zu groß"):
        deskmat.check_size("deskmat-120x60", 600, 0)
    with pytest.raises(ValueError):
        deskmat.check_size("playmat", 150, 0)
    # 4:3 source into 1.72:1 -> full width, centred vertically
    assert deskmat.crop_box((1200, 900), 610 / 355) == (0, 101, 1200, 799)
    # zoom 2 (600 x 300 window), pushed to the bottom-right corner stays inside
    assert deskmat.crop_box((1200, 900), 2.0, 1.0, 1.0, 2.0) == (600, 600, 1200, 900)
    opts = deskmat.formats()
    assert opts["upscaler"] is False and opts["generator"] == "image.pollinations.ai" and len(opts["styles"]) == 6
    assert opts["dpis"] == [300, 600] and opts["bleeds"] == [0, 3, 5] and opts["formats"][0]["mm"] == [610, 355]


async def test_card_source_and_render_without_upscaler(small_format):
    p = await deskmat.from_card("Sol Ring")
    assert p["source"]["kind"] == "card" and p["source"]["scan"] and p["title"] == "Sol Ring"
    assert deskmat.file(p["id"], "source").exists()
    done = await deskmat.render(p["id"], fmt=small_format, dpi=600, bleed_mm=3)
    res = done["result"]
    assert res["size"] == [1559, 969] and res["dpi"] == 600 and res["print_mm"] == [66, 41] and res["ai_passes"] == 0
    assert any("Real-ESRGAN ist nicht eingerichtet" in w for w in res["warnings"])
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.size == (1559, 969) and round(im.info["dpi"][0]) == 600
    assert deskmat.file(p["id"], "result").name == "deskmat-1559x969-600dpi.png"
    with Image.open(deskmat.file(p["id"], "preview")) as im:
        assert max(im.size) == 1200


async def test_render_with_real_esrgan_one_and_two_passes(tmp_path, small_format):
    _fake_esrgan(tmp_path)
    p = deskmat.from_upload(_png((400, 240)), "Mein Drache.png")
    assert p["title"] == "Mein Drache" and p["source"]["size"] == [400, 240]
    # factor 709 / 267 = 2.7 -> one pass (x4) then down to the exact size
    done = await deskmat.render(p["id"], fmt=small_format, fit="fill", crop={"cx": 0.8, "cy": 0.2, "zoom": 1.5})
    res = done["result"]
    assert res["ai_passes"] == 1 and res["size"] == [709, 413] and res["warnings"] == []
    assert res["source_px"] == [267, 155]  # 400 x 240 source, window 1.72:1, zoom 1.5
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.getpixel((700, 10))[0] > 150  # the yellow top-right corner is in the crop
    # 600 DPI: factor 1417 / 267 = 5.3 -> one pass by default (more natural) ...
    one = await deskmat.render(p["id"], fmt=small_format, dpi=600, crop={"cx": 0.8, "cy": 0.2, "zoom": 1.5})
    assert one["result"]["ai_passes"] == 1 and one["render"]["passes"] == 1
    # ... two when asked for: the second one starts from a quarter of the target
    two = await deskmat.render(p["id"], fmt=small_format, dpi=600, crop={"cx": 0.8, "cy": 0.2, "zoom": 1.5}, filetype="jpg", passes=2)
    assert two["result"]["ai_passes"] == 2 and two["result"]["size"] == [1417, 827]
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.format == "JPEG" and im.size == (1417, 827) and round(im.info["dpi"][0]) == 600
    assert not list(deskmat._dir(p["id"]).glob("upscale-in-*"))  # intermediates are cleaned up
    fit = await deskmat.render(p["id"], fmt=small_format, fit="fit")
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.size == (709, 413)
        edge, centre = im.getpixel((3, 206)), im.getpixel((354, 380))
        assert sum(edge) < sum(centre) + 60  # darkened blurred extension at the sides
    assert fit["render"]["fit"] == "fit" and len(list(deskmat._dir(p["id"]).glob("deskmat-*"))) == 1


async def test_too_small_motif_warns(small_format):
    p = deskmat.from_upload(_png((80, 64)), "klein.png")
    res = (await deskmat.render(p["id"], fmt=small_format, dpi=600, upscale=False))["result"]
    assert any("klein für 600 DPI" in w for w in res["warnings"])


async def test_upload_rejects_non_images():
    with pytest.raises(ValueError):
        deskmat.from_upload(b"not an image", "x.txt")


async def test_generate_variants_and_choose():
    p = await deskmat.generate("Nebelburg", "misty castle, wide panorama", "playmat", variants=2)
    assert len(p["candidates"]) == 2 and p["candidates"][0]["size"] == [1536, 896]
    assert deskmat.gen_size("deskmat-90x40") == (1536, 704)
    chosen = deskmat.choose(p["id"], 1)
    assert chosen["source"]["kind"] == "generated" and chosen["source"]["chosen"] == 1 and not chosen["source"]["scan"]
    with pytest.raises(ValueError):
        deskmat.choose(p["id"], 5)
    settings.update({"image_generator_url": "https://image.pollinations.ai/fail/{prompt}?width={width}&height={height}&seed={seed}"})
    with pytest.raises(RuntimeError, match="kein Bild"):
        await deskmat.generate("x", "y", "playmat", variants=1)
    assert [m["title"] for m in deskmat.projects()] == ["Nebelburg"]  # the failed project is gone


async def test_mpc_scan_starts_on_the_art_box():
    settings.update({"mpcfill_server": "https://mpc.test"})
    opts = await deskmat.mpc_options("Sol Ring")
    assert [o["id"] for o in opts] == ["drive-sol-1", "drive-sol-2"] and opts[0]["dpi"] == 1200
    p = await deskmat.from_card("Sol Ring", mpc_id="drive-sol-1")
    assert p["source"]["kind"] == "mpc" and p["source"]["size"] == [1644, 2244]
    crop = deskmat.default_crop(p, 610 / 355)
    assert crop["cy"] == pytest.approx(0.3325) and crop["zoom"] == pytest.approx(1.205, abs=0.01)


def test_prompt_and_routes(monkeypatch, small_format):
    req = DeskmatGenerateRequest(setting="Eine Burg im Nebel über einem Drachenfriedhof", style="dark")
    text = deskmat_prompt(req, {"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "description": "Friedhof"})
    assert "Drachenfriedhof" in text and "dark gothic" in text and "no text" in text and "Meren of Clan Nel Toth" in text

    async def fake_run(job, prompt, model, output_format=None, *, read_only=False, finish=None):
        assert read_only and output_format["schema"]["required"] == ["title", "prompt"]
        await finish(job, True, "", {"title": "Nebelburg", "prompt": "misty castle over a dragon graveyard, no text"})
        job.done = True

    monkeypatch.setattr(app_mod, "_run_claude", fake_run)
    client = TestClient(app)
    assert client.get("/api/deskmat/options").json()["formats"][0]["key"] == "playmat"
    job = client.post("/api/deskmat/generate", json={"setting": "Burg im Nebel", "variants": 2}).json()["job"]
    for _ in range(100):
        if any(e["type"] == "done" for e in app_mod.JOBS[job].events):
            break
        time.sleep(0.02)
    project = next(e["project"] for e in app_mod.JOBS[job].events if e["type"] == "deskmat")
    pid = project["id"]
    assert client.get(f"/api/deskmat/{pid}/image", params={"kind": "candidate", "n": 1}).status_code == 200
    assert client.post(f"/api/deskmat/{pid}/choose", json={"n": 0}).json()["source"]["file"].startswith("source.")
    job = client.post(f"/api/deskmat/{pid}/render", json={"format": small_format, "dpi": 300, "bleed_mm": 5}).json()["job"]
    for _ in range(300):
        if any(e["type"] == "done" for e in app_mod.JOBS[job].events):
            break
        time.sleep(0.02)
    assert any(e["type"] == "deskmat" for e in app_mod.JOBS[job].events)
    r = client.get(f"/api/deskmat/{pid}/image", params={"kind": "result", "download": True})
    assert r.status_code == 200 and 'filename="Nebelburg-827x531-300dpi.png"' in r.headers["content-disposition"]
    up = client.post("/api/deskmat/upload", params={"filename": "bild.png"}, content=_png())
    assert up.status_code == 200 and len(client.get("/api/deskmats").json()) == 2
    assert client.post("/api/deskmat/card", json={"name": "Sol Ring"}).json()["source"]["kind"] == "card"
    assert client.delete(f"/api/deskmat/{pid}").json() == {"deleted": True}
    assert client.get(f"/api/deskmat/{pid}").status_code == 404
    assert client.get("/api/deskmat/../../etc").status_code == 404
    assert client.post(f"/api/deskmat/{up.json()['id']}/render", json={"dpi": 1000}).status_code == 400
    assert "zu groß" in client.post(f"/api/deskmat/{up.json()['id']}/render", json={"format": "deskmat-120x60", "dpi": 600}).json()["detail"]


async def test_mcp_create_deskmat(small_format):
    res = await mcp.call_tool("create_deskmat", {"card": "Sol Ring", "format": small_format, "dpi": 600})
    assert "deskmat-1417x827-600dpi.png" in str(res)
    assert "zu groß" in str(await mcp.call_tool("create_deskmat", {"card": "Sol Ring", "format": "deskmat-120x60", "dpi": 600}))
    assert "error" in str(await mcp.call_tool("create_deskmat", {}))


def test_generator_setting():
    client = TestClient(app)
    assert client.post("/api/settings", json={"image_generator_url": "https://x.test/img"}).status_code == 400
    ok = client.post("/api/settings", json={"image_generator_url": "http://localhost:7860/gen?p={prompt}&s={seed}&x={other}"})
    assert ok.status_code == 200
    assert deskmat.generator_url("a b", 10, 20, 7) == "http://localhost:7860/gen?p=a%20b&s=7&x={other}"
    assert deskmat.formats()["generator"] == "localhost:7860"


async def test_compare_and_testprint(tmp_path, small_format, monkeypatch):
    _fake_esrgan(tmp_path)
    monkeypatch.setattr(deskmat, "COMPARE_PX", 200)
    p = deskmat.from_upload(_png((300, 180)), "Turm.png")
    with pytest.raises(ValueError, match="Erst"):
        await deskmat.compare(p["id"])
    await deskmat.render(p["id"], fmt=small_format, dpi=600)  # factor 1417 / 300 = 4.7 -> two passes possible
    done = await deskmat.compare(p["id"], x=0.9, y=0.1)
    cmp = done["compare"]
    assert [t["passes"] for t in cmp["tiles"]] == [0, 1, 2]
    assert cmp["box"] == [1175, 0, 1375, 200] and cmp["tile_mm"] == 8  # x centred at 90 %, y clamped to the top edge
    for passes in (0, 1, 2):
        with Image.open(deskmat.compare_file(p["id"], passes)) as im:
            assert im.size == (200, 200)
    with Image.open(deskmat.compare_file(p["id"], 1)) as im:
        assert im.getpixel((150, 50))[0] > 150  # the yellow top-right corner of the source
    # test print: the finished print is only 60 x 35 mm, so the piece is the whole print on an A4 sheet
    pdf = deskmat.testprint(p["id"], x=0.5, y=0.5).read_bytes()
    assert pdf.startswith(b"%PDF")
    box = [float(v) for v in re.search(rb"/MediaBox \[ ?([\d. ]+)\]", pdf).group(1).split()]
    assert box[2] == pytest.approx(841.9, abs=0.5) and box[3] == pytest.approx(595.3, abs=0.5)  # A4 landscape in pt


def test_testprint_region_at_real_size(small_format, monkeypatch):
    monkeypatch.setitem(deskmat.TEST_MM, "A4", (40, 20))
    p = deskmat.from_upload(_png((900, 600)), "Reiter.png")
    asyncio.run(deskmat.render(p["id"], fmt=small_format, upscale=False))
    client = TestClient(app)
    r = client.get(f"/api/deskmat/{p['id']}/testprint", params={"x": 0.2, "y": 0.8})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert 'filename="Probedruck-Reiter.pdf"' in r.headers["content-disposition"]
    assert client.post(f"/api/deskmat/{p['id']}/compare", json={"x": 2}).status_code == 422
    assert client.get(f"/api/deskmat/{p['id']}/compare/1").status_code == 404
