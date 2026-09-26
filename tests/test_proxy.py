"""Print studio: image plan, picker, prepare (order XML), PDF, MPC Autofill launch + console."""

import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import MPC_SERVER
from fastapi.testclient import TestClient
from PIL import Image

from mtgdeck import imaging, proxy, settings
from mtgdeck.gui import app as gui

DECK = {
    "slug": "meren-print",
    "name": "Meren Print",
    "commanders": ["Meren of Clan Nel Toth"],
    "cards": [
        {"name": "Sol Ring", "qty": 1},
        {"name": "Delver of Secrets // Insectile Aberration", "qty": 1},
        {"name": "Forest", "qty": 3},
        {"name": "Totally Unknown Card", "qty": 1},
    ],
}


def test_imaging_bleed_cardback_pdf(tmp_path):
    src = tmp_path / "scan.png"
    Image.new("RGBA", (745, 1040), (0, 0, 0, 0)).save(src)
    out = imaging.add_bleed(src, tmp_path / "out.jpg")
    with Image.open(out) as im:
        assert im.size == imaging.FULL_PX == (822, 1122)
        assert im.mode == "RGB"
    back = imaging.plain_cardback(tmp_path / "back.jpg")
    info = imaging.make_pdf([out] * 10 + [back], tmp_path / "x.pdf", paper="Letter")
    assert info == {"cards": 11, "pages": 2}
    assert (tmp_path / "x.pdf").read_bytes().startswith(b"%PDF")


def test_process_query_and_bracket():
    assert proxy.process_query("Atraxa, Praetors' Voice") == "atraxa praetors voice"
    assert proxy.mpc_bracket(100) == 108 and proxy.mpc_bracket(18) == 18 and proxy.mpc_bracket(700) == 612
    assert proxy._png_url("https://cards.scryfall.io/normal/front/a/b/x.jpg?123") == "https://cards.scryfall.io/png/front/a/b/x.png"


async def test_plan_scryfall_only_with_dfc_and_missing():
    p = await proxy.plan(DECK)
    assert p["quantity"] == 7 and p["mpc_bracket"] == 18 and not p["server"]
    by = {c["name"]: c for c in p["cards"]}
    assert by["Meren of Clan Nel Toth"]["commander"]
    assert by["Forest"]["qty"] == 3
    delver = by["Delver of Secrets // Insectile Aberration"]
    assert delver["front"]["face"] == "Delver of Secrets" and delver["back"]["face"] == "Insectile Aberration"
    assert delver["back"]["image"]["full"] == "https://cards.scryfall.io/png/back/delver.png"
    assert p["missing"] == ["Totally Unknown Card"]


async def test_plan_with_mpc_server_and_own_choice():
    settings.update({"mpcfill_server": MPC_SERVER})
    p = await proxy.plan(DECK)
    by = {c["name"]: c for c in p["cards"]}
    assert p["server"]
    assert by["Sol Ring"]["front"]["image"]["origin"] == "mpcfill"
    assert by["Sol Ring"]["front"]["image"]["full"].startswith("https://cdn.mpcautofill.com/images/google_drive/full/drive-sol-1.jpg")
    assert by["Sol Ring"]["front"]["mpc_hits"] == 2
    assert by["Delver of Secrets // Insectile Aberration"]["back"]["image"]["id"] == "drive-insect"
    assert by["Forest"]["front"]["image"]["origin"] == "scryfall"

    opts = await proxy.alternatives(DECK, "Sol Ring")
    assert [o["origin"] for o in opts] == ["mpcfill", "mpcfill", "scryfall", "scryfall", "scryfall"]
    proxy.choose("meren-print", "Sol Ring", opts[3])
    p = await proxy.plan(DECK)
    sol = next(c for c in p["cards"] if c["name"] == "Sol Ring")["front"]["image"]
    assert sol["custom"] and sol["origin"] == "scryfall"
    # 'scryfall' source ignores MPC picks but keeps Scryfall picks
    proxy.choose("meren-print", "Sol Ring", opts[1])
    p = await proxy.plan(DECK, source="scryfall")
    assert next(c for c in p["cards"] if c["name"] == "Sol Ring")["front"]["image"]["origin"] == "scryfall"
    proxy.choose("meren-print", "Sol Ring", None)
    assert proxy.load_selection("meren-print") == {}


async def test_prepare_writes_valid_order_and_pdf():
    settings.update({"mpcfill_server": MPC_SERVER})
    seen = []
    r = await proxy.prepare(DECK, stock="(S33) Superior Smooth", foil=True, progress=lambda d, t, n: seen.append((d, t)))
    assert seen[-1][0] == seen[-1][1] == r["images_mpcfill"] + r["images_scryfall"]
    assert r["quantity"] == 6 and r["missing"] == ["Totally Unknown Card"]
    assert r["cardback"].startswith("Standard-Kartenrücken")

    root = ET.parse(r["xml"]).getroot()
    assert root.tag == "order"
    assert root.findtext("details/quantity") == "6"
    assert root.findtext("details/stock") == "(S33) Superior Smooth" and root.findtext("details/foil") == "true"
    fronts = root.findall("fronts/card")
    slots = sorted(int(x) for c in fronts for x in c.findtext("slots").split(","))
    assert slots == list(range(6))
    for c in fronts + root.findall("backs/card"):
        assert c.findtext("sourceType") == "Local File"
        assert Path(c.findtext("id")).is_file() and c.findtext("name") == Path(c.findtext("id")).name
    backs = root.findall("backs/card")
    assert len(backs) == 1 and backs[0].findtext("query") == "insectile aberration"
    assert Path(root.findtext("cardback")).is_file()
    # Scryfall scans got a bleed edge
    forest = next(c for c in fronts if c.findtext("query") == "forest")
    with Image.open(forest.findtext("id")) as im:
        assert im.size == imaging.FULL_PX
    assert forest.findtext("slots").count(",") == 2

    pdf = proxy.export_pdf("meren-print")
    assert pdf["cards"] == 7 and pdf["pages"] == 1  # 6 fronts + 1 DFC back
    assert proxy.export_pdf("meren-print", include_backs=False)["cards"] == 6


async def test_prepare_validates_stock():
    with pytest.raises(ValueError):
        await proxy.prepare(DECK, stock="Cardboard")
    with pytest.raises(ValueError):
        await proxy.prepare(DECK, stock="(P10) Plastic", foil=True)


def test_autofill_command(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        proxy.autofill_command(tmp_path)
    exe = tmp_path / "autofill-windows.exe"
    exe.write_text("x")
    settings.update({"autofill_path": str(exe)})
    if sys.platform != "win32":
        with pytest.raises(RuntimeError):
            proxy.autofill_command(tmp_path)
    script = tmp_path / "autofill.py"
    script.write_text("print('hi')")
    settings.update({"autofill_path": str(script), "browser": "edge"})
    cmd = proxy.autofill_command(tmp_path, mode="pdf")
    assert cmd[:2] == [sys.executable, str(script)]
    assert cmd[cmd.index("--directory") + 1] == str(tmp_path) and "--exportpdf" in cmd
    assert cmd[cmd.index("--browser") + 1] == "edge" and "--no-image-post-processing" in cmd


FAKE_AUTOFILL = """
import sys
from InquirerPy import inquirer
print("MPC Autofill desktop tool has successfully initialised!", flush=True)
print("args:", " ".join(sys.argv[1:]), flush=True)
# the real tool asks exactly this with an arrow-key menu (src/driver.py, execute_orders)
choice = inquirer.select(
    message="How would you like to upload this order?",
    choices=["Create a new project (default)", "Add more cards to an existing project",
             "Continue editing an existing project"],
    default="Create a new project (default)",
).execute()
print("CHOICE:", choice, flush=True)
answer = input("Press Enter to close this window - your browser window will remain open.\\n")
print("got:", repr(answer), flush=True)
"""

ANSI = __import__("re").compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\r")


def _screen(job):
    return ANSI.sub("", "".join(e.get("data", "") for e in job.events if e["type"] == "term"))


def _wait(job_id, cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        job = gui.JOBS[job_id]
        if cond(job):
            return job
        time.sleep(0.05)
    raise AssertionError([e for e in gui.JOBS[job_id].events])


def test_gui_print_studio_flow(tmp_path):
    from mtgdeck import storage

    storage.save({k: v for k, v in DECK.items() if k != "slug"} | {"name": "Meren Print", "bracket": 2})
    script = tmp_path / "autofill.py"
    script.write_text(FAKE_AUTOFILL)
    settings.update({"autofill_path": str(script)})

    with TestClient(gui.app) as client:
        s = client.get("/api/settings").json()
        assert s["autofill_found"] == str(script) and "(S30) Standard Smooth" in s["stocks"]

        plan = client.get("/api/decks/meren-print/print/plan").json()
        assert plan["quantity"] == 7
        alts = client.get("/api/decks/meren-print/print/alternatives", params={"card": "Sol Ring"}).json()
        assert len(alts) == 3  # no server -> Scryfall printings
        client.post("/api/decks/meren-print/print/choose", json={"face": "Sol Ring", "option": alts[2]})

        assert client.post("/api/decks/meren-print/print/autofill", json={}).status_code == 400  # not prepared yet

        job = client.post("/api/decks/meren-print/print/prepare", json={"source": "auto"}).json()["job"]
        j = _wait(job, lambda j: j.done)
        types = [e["type"] for e in j.events]
        assert "progress" in types and "print" in types
        assert client.head("/api/decks/meren-print/print/files/xml").status_code == 200

        r = client.post("/api/decks/meren-print/print/pdf", json={"paper": "A4"}).json()
        assert r["pages"] == 1
        assert client.get("/api/decks/meren-print/print/files/pdf").content.startswith(b"%PDF")

        job = client.post("/api/decks/meren-print/print/autofill", json={"mode": "mpc"}).json()["job"]
        _wait(job, lambda j: "How would you like to upload this order?" in _screen(j))
        assert "--directory" in _screen(gui.JOBS[job]) and "--no-image-post-processing" in _screen(gui.JOBS[job])
        # arrow down + Enter in the menu, exactly what xterm.js sends from the browser
        assert client.post(f"/api/jobs/{job}/input", json={"text": "\x1b[B", "raw": True}).json() == {"ok": True}
        client.post(f"/api/jobs/{job}/input", json={"text": "\r", "raw": True})
        _wait(job, lambda j: "Press Enter to close" in _screen(j))
        assert "CHOICE: Add more cards to an existing project" in _screen(gui.JOBS[job])
        client.post(f"/api/jobs/{job}/resize", json={"rows": 40, "cols": 120})
        client.post(f"/api/jobs/{job}/input", json={"text": "ok"})  # line mode: text + Enter
        j = _wait(job, lambda j: j.done)
        assert "got: 'ok'" in _screen(j)
        assert j.events[-1] == {"type": "done", "ok": True}
        assert client.post(f"/api/jobs/{job}/input", json={"text": "x"}).status_code == 409

        # own console window on request
        r = client.post("/api/decks/meren-print/print/autofill", json={"mode": "mpc", "window": True}).json()
        assert r["window"] is True and r["started"]


def test_settings_creates_parent_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "nested" / "dir" / "s.json")
    assert settings.update({"browser": "edge", "unknown": 1})["browser"] == "edge"
    monkeypatch.setenv("MTG_MPCFILL_SERVER", "https://env.example")
    assert settings.load()["mpcfill_server"] == "https://env.example"


FAKE_ESRGAN = """
import sys
from PIL import Image
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
assert args["-s"] == "4" and args["-n"] in ("realesrgan-x4plus", "realesrnet-x4plus"), args
assert args["-m"].endswith("models") and args["-f"] == "png", args
if "fail" in open(__file__).read().split("#flags:")[-1]:
    sys.exit("vkCreateInstance failed -9\\ninvalid gpu device")
with Image.open(args["-i"]) as im:
    im.resize((im.width * 4, im.height * 4)).save(args["-o"])
#flags:
"""


def _fake_esrgan(tmp_path, fail=False):
    exe = tmp_path / "realesrgan" / "realesrgan.py"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(FAKE_ESRGAN + ("fail" if fail else ""))
    settings.update({"upscaler_path": str(exe)})
    return exe


def _forest_size(result):
    root = ET.parse(result["xml"]).getroot()
    forest = next(c for c in root.findall("fronts/card") if c.findtext("query") == "forest")
    with Image.open(forest.findtext("id")) as im:
        return im.size


async def test_upscaling_is_opt_in(tmp_path):
    assert settings.load()["upscale"] is False
    r = await proxy.prepare(DECK)  # default: no upscaling, no upscaler needed
    assert not r["upscaled"] and _forest_size(r) == (822, 1122)
    with pytest.raises(ValueError, match="Real-ESRGAN"):
        await proxy.prepare(DECK, upscale=True)  # opted in, but tool missing -> clear error


async def test_upscaling_to_600_dpi(tmp_path):
    _fake_esrgan(tmp_path)
    r = await proxy.prepare(DECK, upscale=True)
    assert r["upscaled"] and r["images_upscaled"] == r["images_scryfall"] > 0
    assert _forest_size(r) == imaging.sizes(600)[2] == (1644, 2244)
    assert any("hochskaliert" in w for w in r["warnings"])
    # setting as default, then explicit opt-out per run
    settings.update({"upscale": True})
    assert (await proxy.prepare(DECK))["upscaled"]
    assert _forest_size(await proxy.prepare(DECK, upscale=False)) == (822, 1122)


async def test_upscaler_failure_falls_back_to_300_dpi(tmp_path):
    _fake_esrgan(tmp_path, fail=True)
    r = await proxy.prepare(DECK, upscale=True)
    assert r["errors"] == [] and r["images_upscaled"] == 0
    assert _forest_size(r) == (822, 1122)  # card kept, just not upscaled
    gpu = [w for w in r["warnings"] if "Vulkan-fähige Grafikkarte" in w]
    assert len(gpu) == 1 and "300 DPI" in gpu[0]  # one aggregated message, not one per card


def test_upscale_models_from_folder(tmp_path):
    exe = tmp_path / "realesrgan-ncnn-vulkan.exe"
    exe.write_text("x")
    assert proxy.upscale_models(exe) == []
    (tmp_path / "models").mkdir()
    for name in ("realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3-x2"):
        (tmp_path / "models" / f"{name}.param").write_text("")
        (tmp_path / "models" / f"{name}.bin").write_text("")
    assert proxy.upscale_models(exe) == ["realesrgan-x4plus", "realesrgan-x4plus-anime"]


def test_find_upscaler_in_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "PROJECT_ROOT", tmp_path)
    assert proxy.find_upscaler() is None
    exe = tmp_path / "tools" / "realesrgan-ncnn-vulkan-20220424-windows" / "realesrgan-ncnn-vulkan.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    assert proxy.find_upscaler() == exe
    cmd = proxy.upscale_command(exe, Path("a.png"), Path("b.png"), "realesrgan-x4plus")
    assert cmd[0] == str(exe) and cmd[cmd.index("-m") + 1] == str(exe.parent / "models")


async def test_readable_print_files_and_prepared_info(tmp_path):
    settings.update({"mpcfill_server": MPC_SERVER})
    _fake_esrgan(tmp_path)
    r = await proxy.prepare(DECK, upscale=True)
    images = Path(r["images_dir"])
    assert images == proxy.order_dir("meren-print") / "images"
    names = sorted(p.name for p in images.iterdir())
    assert names == ["Delver of Secrets.jpg", "Forest.jpg", "Insectile Aberration.jpg",
                     "Meren of Clan Nel Toth.jpg", "Sol Ring.jpg", "_Kartenrücken.jpg"]  # fmt: skip
    # the order references the readable files (so the tool's log shows card names)
    root = ET.parse(r["xml"]).getroot()
    ids = {c.findtext("id") for c in root.findall("fronts/card") + root.findall("backs/card")}
    assert ids <= {str(p) for p in images.iterdir()} and root.findtext("cardback") == str(images / "_Kartenrücken.jpg")

    info = proxy.load_prepared("meren-print")
    forest, sol = info["faces"]["Forest"], info["faces"]["Sol Ring"]
    assert forest["upscaled"] and forest["dpi"] == 600 and forest["origin"] == "scryfall"
    assert forest["original"].endswith(".png") and Path(forest["original"]).is_file()
    assert Path(forest["file"]).samefile(forest["cache"]) or Path(forest["file"]).stat().st_size == Path(forest["cache"]).stat().st_size
    assert not sol["upscaled"] and sol["origin"] == "mpcfill" and sol["dpi"] == 1200
    with Image.open(proxy.prepared_image("meren-print", "Forest", "original")) as im:
        assert im.size == (745, 1040)
    with Image.open(proxy.prepared_image("meren-print", "Forest", "file")) as im:
        assert im.size == (1644, 2244)
    with pytest.raises(FileNotFoundError):
        proxy.prepared_image("meren-print", "Nope", "file")

    # re-preparing a smaller deck removes stale files
    small = {**DECK, "cards": [{"name": "Forest", "qty": 2}]}
    await proxy.prepare(small)
    assert sorted(p.name for p in images.iterdir()) == ["Forest.jpg", "Meren of Clan Nel Toth.jpg", "_Kartenrücken.jpg"]


def test_gui_compare_and_open_folder(tmp_path, monkeypatch):
    import asyncio

    from mtgdeck import storage

    storage.save({k: v for k, v in DECK.items() if k != "slug"} | {"name": "Meren Print", "bracket": 2})
    asyncio.run(proxy.prepare(DECK))
    opened = []
    monkeypatch.setattr(gui, "open_in_file_manager", lambda p: opened.append(p))
    client = TestClient(gui.app)
    prepared = client.get("/api/decks/meren-print/print/prepared").json()
    assert "Forest" in prepared["faces"] and prepared["images_dir"].endswith("images")

    from io import BytesIO

    def size(kind):
        r = client.get("/api/decks/meren-print/print/image", params={"face": "Forest", "kind": kind})
        assert r.status_code == 200
        return Image.open(BytesIO(r.content)).size

    assert size("original") == (745, 1040)
    assert size("file") == (822, 1122)
    assert size("trim") == (750, 1050)  # bleed cut off -> aligned with the original for comparison
    assert client.get("/api/decks/meren-print/print/image", params={"face": "Nope"}).status_code == 404
    assert client.get("/api/decks/meren-print/print/image", params={"face": "../../etc/passwd"}).status_code == 404

    r = client.post("/api/decks/meren-print/print/open-folder").json()
    assert r["opened"] and opened == [proxy.order_dir("meren-print") / "images"]
    assert client.post("/api/decks/unknown/print/open-folder").status_code == 404


def _halftone_card(size=(420, 600)):
    import numpy as np

    w, h = size
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    base = 90 + 80 * xx / w  # smooth gradient ("sky")
    base[:, w // 2:] = np.where(yy[:, w // 2:] % 60 < 30, 20, 235)  # hard edges ("text")
    screen = 30 * np.cos(2 * np.pi * (0.17 * xx + 0.297 * yy)) + 25 * np.cos(2 * np.pi * (0.297 * xx - 0.173 * yy))
    img = np.clip(base + screen * (xx < w // 2), 0, 255).astype("uint8")
    return Image.fromarray(np.stack([img] * 3, axis=2), "RGB"), base


def test_descreen_removes_halftone_keeps_edges():
    import numpy as np

    img, base = _halftone_card()
    out = np.asarray(imaging.descreen(img, "normal").convert("L"), dtype=np.float32)
    src = np.asarray(img.convert("L"), dtype=np.float32)
    w = img.width
    inner = (slice(20, -20), slice(20, w // 2 - 20))  # away from the abrupt synthetic border
    noise_before = np.abs(src[inner] - base[inner]).mean()
    noise_after = np.abs(out[inner] - base[inner]).mean()
    assert noise_after < noise_before / 4  # screen pattern largely gone
    edges = out[:, w // 2 + 20 : -20]
    dark, light = edges[(np.arange(600) % 60 < 25)[:, None].repeat(edges.shape[1], 1)], edges[(np.arange(600) % 60 >= 35)[:, None].repeat(edges.shape[1], 1)]
    assert light.mean() - dark.mean() > 0.9 * (235 - 20)  # hard edges keep their contrast
    # an unscreened image with irregular hard edges (like art + text) passes through (almost) unchanged
    rng = np.random.default_rng(1)
    clean = np.full((600, 420), 128, np.float32) + np.linspace(0, 60, 420)[None, :]
    for _ in range(40):
        x, y = rng.integers(0, 380), rng.integers(0, 560)
        clean[y : y + rng.integers(3, 40), x : x + rng.integers(2, 40)] = rng.integers(0, 255)
    clean_img = Image.fromarray(np.stack([clean.astype("uint8")] * 3, axis=2), "RGB")
    diff = np.abs(np.asarray(imaging.descreen(clean_img), dtype=np.float32) - np.asarray(clean_img, dtype=np.float32))
    assert diff.mean() < 1.0


async def test_upscale_uses_descreened_input(tmp_path):
    settings.update({"descreen": "normal"})
    exe = _fake_esrgan(tmp_path)
    exe.write_text(exe.read_text().replace('with Image.open(args["-i"]) as im:',
                                           'open(args["-o"] + ".src", "w").write(args["-i"])\nwith Image.open(args["-i"]) as im:'))
    r = await proxy.prepare(DECK, upscale=True)
    info = proxy.load_prepared("meren-print")
    assert info["descreen"] == "normal"
    forest_up = sorted(Path(info["faces"]["Forest"]["cache"]).parent.glob("*-dsnormal-x4-*.png.src"))
    assert forest_up and forest_up[0].read_text().endswith("-dsnormal.png")
    assert "Druckraster entfernt: normal" in " ".join(r["warnings"])

    settings.update({"descreen": "off"})
    await proxy.prepare(DECK, upscale=True)
    assert proxy.load_prepared("meren-print")["faces"]["Forest"]["file"]  # works without descreening too
