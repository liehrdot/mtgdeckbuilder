"""Deskmat studio: sources, crop maths, rendering to ~4K with (fake) Real-ESRGAN, generator, routes."""

import asyncio
import io
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


def test_formats_and_crop_maths():
    assert deskmat.target_size("playmat", 4096) == (4096, 2384)
    assert deskmat.target_size("screen-16x9", 3840) == (3840, 2160)
    assert deskmat.dpi("playmat", (4096, 2384)) == 171
    # 4:3 source into 1.72:1 -> full width, centred vertically
    assert deskmat.crop_box((1200, 900), 610 / 355) == (0, 101, 1200, 799)
    # zoom 2 (600 x 300 window), pushed to the bottom-right corner stays inside
    assert deskmat.crop_box((1200, 900), 2.0, 1.0, 1.0, 2.0) == (600, 600, 1200, 900)
    opts = deskmat.formats()
    assert opts["upscaler"] is False and opts["generator"] == "image.pollinations.ai" and len(opts["styles"]) == 6


async def test_card_source_and_render_without_upscaler():
    p = await deskmat.from_card("Sol Ring")
    assert p["source"]["kind"] == "card" and p["source"]["scan"] and p["title"] == "Sol Ring"
    assert deskmat.file(p["id"], "source").exists()
    done = await deskmat.render(p["id"], fmt="playmat", long_px=4096)
    res = done["result"]
    assert res["size"] == [4096, 2384] and not res["ai_upscaled"]
    assert any("Real-ESRGAN ist nicht eingerichtet" in w for w in res["warnings"])
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.size == (4096, 2384)
    with Image.open(deskmat.file(p["id"], "preview")) as im:
        assert max(im.size) == 1200


async def test_render_with_real_esrgan_and_fit(tmp_path):
    _fake_esrgan(tmp_path)
    p = deskmat.from_upload(_png((1000, 700)), "Mein Drache.png")
    assert p["title"] == "Mein Drache" and p["source"]["size"] == [1000, 700]
    done = await deskmat.render(p["id"], fmt="deskmat-90x40", long_px=3840, fit="fill", crop={"cx": 0.8, "cy": 0.2, "zoom": 1.5})
    res = done["result"]
    assert res["ai_upscaled"] and res["size"] == [3840, 1707] and res["warnings"] == []
    assert res["source_px"] == [667, 296]  # 1000 wide / 2.25 aspect / zoom 1.5
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.getpixel((3800, 20))[0] > 150  # the yellow top-right corner is in the crop
    fit = await deskmat.render(p["id"], fmt="playmat", fit="fit")
    with Image.open(deskmat.file(p["id"], "result")) as im:
        assert im.size == (4096, 2384)
        edge, centre = im.getpixel((5, 1192)), im.getpixel((2048, 1800))
        assert sum(edge) < sum(centre) + 60  # darkened blurred extension at the sides
    assert fit["render"]["fit"] == "fit" and len(list(deskmat._dir(p["id"]).glob("deskmat-*.png"))) == 1


async def test_upload_rejects_non_images():
    with pytest.raises(ValueError):
        deskmat.from_upload(b"not an image", "x.txt")


async def test_generate_variants_and_choose():
    p = await deskmat.generate("Nebelburg", "misty castle, wide panorama", "playmat", variants=2)
    assert len(p["candidates"]) == 2 and p["candidates"][0]["size"] == [1536, 896]
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


def test_prompt_and_routes(monkeypatch):
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
    job = client.post(f"/api/deskmat/{pid}/render", json={"format": "screen-16x9", "long_px": 3840}).json()["job"]
    for _ in range(300):
        if any(e["type"] == "done" for e in app_mod.JOBS[job].events):
            break
        time.sleep(0.02)
    assert any(e["type"] == "deskmat" for e in app_mod.JOBS[job].events)
    r = client.get(f"/api/deskmat/{pid}/image", params={"kind": "result", "download": True})
    assert r.status_code == 200 and 'filename="Nebelburg-3840x2160.png"' in r.headers["content-disposition"]
    up = client.post("/api/deskmat/upload", params={"filename": "bild.png"}, content=_png())
    assert up.status_code == 200 and len(client.get("/api/deskmats").json()) == 2
    assert client.post("/api/deskmat/card", json={"name": "Sol Ring"}).json()["source"]["kind"] == "card"
    assert client.delete(f"/api/deskmat/{pid}").json() == {"deleted": True}
    assert client.get(f"/api/deskmat/{pid}").status_code == 404
    assert client.get("/api/deskmat/../../etc").status_code == 404
    assert client.post(f"/api/deskmat/{up.json()['id']}/render", json={"long_px": 1000}).status_code == 400


async def test_mcp_create_deskmat():
    res = await mcp.call_tool("create_deskmat", {"card": "Sol Ring", "long_px": 3840})
    assert "3840" in str(res) and "deskmat-3840x" in str(res)
    assert "error" in str(await mcp.call_tool("create_deskmat", {}))


def test_generator_setting():
    client = TestClient(app)
    assert client.post("/api/settings", json={"image_generator_url": "https://x.test/img"}).status_code == 400
    ok = client.post("/api/settings", json={"image_generator_url": "http://localhost:7860/gen?p={prompt}&s={seed}&x={other}"})
    assert ok.status_code == 200
    assert deskmat.generator_url("a b", 10, 20, 7) == "http://localhost:7860/gen?p=a%20b&s=7&x={other}"
    assert deskmat.formats()["generator"] == "localhost:7860"
