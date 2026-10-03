"""Deskmat / playmat studio: turn a card artwork, a generated image or an upload into a print file at
300 DPI (minimum for printing) or 600 DPI for the mat's real size, optionally with bleed.

A project lives in ``deskmats/<id>/`` (``MTG_DESKMAT_DIR``)::

    meta.json            title, source info, last render settings and result
    source.<ext>         the chosen motif (original resolution)
    candidates/<n>.<ext> generated variants to choose from
    deskmat-<W>x<H>-<dpi>dpi.(png|jpg)  the result, preview.jpg a small copy for the GUI

Rendering: crop (fill: a window in the target aspect, centre + zoom) or fit (whole image on a blurred,
darkened extension of itself) → descreen for printed card scans → Real-ESRGAN ×4, twice for large factors
(the second pass starts from exactly a quarter of the target, so it lands on the print size) → Lanczos to
the exact size + light sharpening.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageEnhance, ImageFilter

from . import imaging, proxy, scryfall, storage
from .fmt import num as de_num
from . import settings as settings_mod
from .http import HttpError, _throttle, client, download

DESKMAT_DIR = Path(os.environ.get("MTG_DESKMAT_DIR", storage.PROJECT_ROOT / "deskmats"))
Image.MAX_IMAGE_PIXELS = 320_000_000  # print files at 600 DPI are big (our own files and the user's uploads)

# key -> (label, width mm, height mm)
FORMATS: dict[str, tuple[str, int, int]] = {
    "playmat": ("Playmat 61 × 35,5 cm (Standard)", 610, 355),
    "deskmat-80x30": ("Deskmat 80 × 30 cm", 800, 300),
    "deskmat-90x40": ("Deskmat 90 × 40 cm", 900, 400),
    "deskmat-120x60": ("Deskmat 120 × 60 cm", 1200, 600),
}
DPIS = [300, 600]  # 300 = minimum for printing, 600 = best
BLEEDS = [0, 3, 5]  # mm added on every side (Beschnittzugabe) when the print shop asks for it
MAX_MEGAPIXELS = 250  # larger files do not fit into memory comfortably (120 x 60 cm at 600 DPI = 402 MP)
STYLES: dict[str, tuple[str, str]] = {
    "painting": ("Fantasy-Gemälde (wie Magic-Artwork)", "epic fantasy oil painting in the style of Magic: The Gathering card art"),
    "cinematic": ("Filmisch / Matte Painting", "cinematic matte painting, volumetric light, highly detailed"),
    "dark": ("Düster & gotisch", "dark gothic fantasy illustration, moody dramatic lighting"),
    "watercolor": ("Aquarell", "soft watercolor illustration, flowing washes, paper texture"),
    "anime": ("Anime", "anime key visual, vibrant colors, clean line art"),
    "minimal": ("Minimalistisch", "minimalist flat vector illustration, clean shapes, limited palette"),
}  # fmt: skip
DEFAULT_GENERATOR = "https://image.pollinations.ai/prompt/{prompt}?width={width}&height={height}&seed={seed}&nologo=true&model=flux"
GEN_LONG = 1536  # requested size of generated images (long side); the upscaler does the rest
_ID_RE = re.compile(r"^[a-f0-9]{10}$")
_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}

Progress = Callable[[str], None]


# --- formats ---------------------------------------------------------------------------------------


def target_size(fmt: str, dpi: int = 300, bleed_mm: float = 0) -> tuple[int, int]:
    """Pixels of the print file: the mat's size (plus bleed on every side) at ``dpi``."""
    _, w, h = FORMATS[fmt]
    return round((w + 2 * bleed_mm) / 25.4 * dpi), round((h + 2 * bleed_mm) / 25.4 * dpi)


def check_size(fmt: str, dpi: int, bleed_mm: float) -> tuple[int, int]:
    if fmt not in FORMATS:
        raise ValueError(f"Unbekanntes Format: {fmt}")
    if dpi not in DPIS or bleed_mm not in BLEEDS:
        raise ValueError(f"DPI: {DPIS}, Beschnitt: {BLEEDS} mm")
    size = target_size(fmt, dpi, bleed_mm)
    if size[0] * size[1] > MAX_MEGAPIXELS * 1_000_000:
        raise ValueError(f"{FORMATS[fmt][0]} mit {dpi} DPI wären {size[0] * size[1] / 1e6:.0f} Megapixel – zu groß. "
                         "Nimm 300 DPI oder ein kleineres Format.")  # fmt: skip
    return size


def formats() -> dict[str, Any]:
    cfg = settings_mod.load()
    exe = proxy.find_upscaler(cfg)
    return {
        "formats": [{"key": k, "label": v[0], "mm": [v[1], v[2]]} for k, v in FORMATS.items()],
        "dpis": DPIS, "bleeds": BLEEDS, "max_megapixels": MAX_MEGAPIXELS,
        "styles": [{"key": k, "label": v[0]} for k, v in STYLES.items()],
        "upscaler": bool(exe), "upscale_model": cfg["upscale_model"],
        "generator": (cfg.get("image_generator_url") or DEFAULT_GENERATOR).split("/")[2],
        "mpc": bool(cfg.get("mpcfill_server")),
    }  # fmt: skip


# --- projects --------------------------------------------------------------------------------------


def _dir(pid: str) -> Path:
    if not _ID_RE.match(pid or ""):
        raise FileNotFoundError(f"Unbekanntes Deskmat-Projekt: {pid}")
    return DESKMAT_DIR / pid


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def load(pid: str) -> dict[str, Any]:
    path = _dir(pid) / "meta.json"
    if not path.exists():
        raise FileNotFoundError(f"Unbekanntes Deskmat-Projekt: {pid}")
    return json.loads(path.read_text("utf-8"))


def _save(meta: dict[str, Any]) -> dict[str, Any]:
    meta["updated"] = _now()
    from .jsonstore import write_json

    write_json(_dir(meta["id"]) / "meta.json", meta)
    return meta


def new_project(title: str, source: dict[str, Any]) -> dict[str, Any]:
    pid = uuid.uuid4().hex[:10]
    return _save({"id": pid, "title": title.strip()[:120] or "Deskmat", "created": _now(), "source": source,
                  "candidates": [], "render": None, "result": None})  # fmt: skip


def projects() -> list[dict[str, Any]]:
    if not DESKMAT_DIR.exists():
        return []
    out = []
    for meta in DESKMAT_DIR.glob("*/meta.json"):
        try:
            out.append(json.loads(meta.read_text("utf-8")))
        except ValueError:
            continue
    return sorted(out, key=lambda m: m.get("updated") or "", reverse=True)


def delete(pid: str) -> None:
    shutil.rmtree(_dir(pid), ignore_errors=True)


def file(pid: str, kind: str, n: int | None = None) -> Path:
    """Path of a project's image: ``source`` | ``result`` | ``preview`` | ``candidate`` (with ``n``)."""
    meta = load(pid)
    d = _dir(pid)
    name = {"source": meta["source"].get("file"), "result": (meta.get("result") or {}).get("file"),
            "preview": (meta.get("result") or {}).get("preview")}.get(kind)  # fmt: skip
    if kind == "candidate" and n is not None and 0 <= n < len(meta.get("candidates") or []):
        name = meta["candidates"][n]["file"]
    if not name or not (d / name).is_file():
        raise FileNotFoundError(f"{kind} fehlt")
    return d / name


def _store_image(pid: str, raw: Path, stem: str) -> tuple[str, list[int]]:
    """Normalise an image (verify it, keep its format) as ``<stem>.<ext>`` in the project folder."""
    with Image.open(raw) as img:
        img.load()
        size = [img.width, img.height]
        fmt = img.format or "PNG"
    if size[0] < 64 or size[1] < 64:
        raise ValueError("Das Bild ist zu klein.")
    ext = _EXT.get(fmt)
    dest = _dir(pid) / f"{stem}.{ext or 'png'}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ext:
        shutil.copyfile(raw, dest)
    else:  # e.g. GIF/BMP/TIFF -> PNG
        with Image.open(raw) as img:
            img.convert("RGB").save(dest)
    return dest.relative_to(_dir(pid)).as_posix(), size


def _set_source(meta: dict[str, Any], raw: Path, **info: Any) -> dict[str, Any]:
    name, size = _store_image(meta["id"], raw, "source")
    for old in _dir(meta["id"]).glob("source.*"):
        if old.name != name:
            old.unlink()
    meta["source"] = {**meta.get("source", {}), **info, "file": name, "size": size}
    meta["result"] = None
    return _save(meta)


# --- sources ---------------------------------------------------------------------------------------

# art box of a modern card frame inside a full scan with bleed (fractions of width/height)
ART_BOX = (0.085, 0.11, 0.915, 0.555)


async def from_card(name: str, *, scryfall_id: str | None = None, face: str = "front", mpc_id: str | None = None) -> dict[str, Any]:
    """Project from a card's artwork: Scryfall ``art_crop`` of a printing (default: the card's
    representative printing) or a high-resolution MPC Autofill scan (``mpc_id``), cropped to the art box."""
    from .cards import resolve

    found, _, _ = await resolve([name])
    card = next(iter(found.values()), None)
    if not card:
        raise ValueError(f"Karte nicht gefunden: {name}")
    title = card["name"].split(" // ")[1 if face == "back" and " // " in card["name"] else 0]
    cache = imaging_cache()
    if mpc_id:
        cfg = settings_mod.load()
        mpc = proxy.MpcFill(cfg["mpcfill_server"], cfg["mpcfill_cdn"])
        raw = await download(mpc.full(mpc_id), cache / f"mpc-{mpc_id}.jpg")
        meta = new_project(title, {"kind": "mpc", "card": card["name"], "mpc_id": mpc_id, "scan": True, "art_box": ART_BOX})
        return _set_source(meta, raw)
    sid = scryfall_id or _scryfall_id(card)
    if sid:
        url = scryfall.image_url(sid, "art_crop").replace("/front/", f"/{'back' if face == 'back' else 'front'}/")
    else:  # derive the art crop from the card's image URL
        base = card.get("image_back") if face == "back" and card.get("image_back") else card.get("image")
        if not base or "/normal/" not in base:
            raise ValueError(f"Kein Scryfall-Bild für {card['name']}")
        url = base.replace("/normal/", "/art_crop/")
    key = sid or re.sub(r"[^\w-]+", "_", url.split("/art_crop/")[1].split("?")[0])[:80]
    raw = await download(url, cache / f"art_crop-{key}-{face}.jpg")
    meta = new_project(title, {"kind": "card", "card": card["name"], "scryfall_id": sid, "face": face, "scan": True})
    return _set_source(meta, raw)


def _scryfall_id(card: dict[str, Any]) -> str | None:
    m = re.search(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jpg", card.get("image") or "")
    return m.group(1) if m else None


def imaging_cache() -> Path:
    """The shared image cache of the proxy printing (``<MTG_DATA_DIR>/images``)."""
    path = proxy.image_cache()
    path.mkdir(parents=True, exist_ok=True)
    return path


async def mpc_options(name: str) -> list[dict[str, Any]]:
    """High-resolution scans of a card on the configured MPC Autofill server: ``[{id, thumb, dpi, source}]``."""
    cfg = settings_mod.load()
    if not cfg.get("mpcfill_server"):
        return []
    mpc = proxy.MpcFill(cfg["mpcfill_server"], cfg["mpcfill_cdn"])
    hits = (await mpc.search([name])).get(name) or []
    details = await mpc.cards(hits[:40]) if hits else {}
    return [{"id": i, "thumb": mpc.thumb(i), "dpi": (details.get(i) or {}).get("dpi"), "source": (details.get(i) or {}).get("sourceName")}
            for i in hits[:40]]  # fmt: skip


def from_upload(data: bytes, filename: str = "") -> dict[str, Any]:
    if len(data) > 60 * 1024 * 1024:
        raise ValueError("Das Bild ist größer als 60 MB.")
    tmp = imaging_cache() / f"upload-{uuid.uuid4().hex}.bin"
    tmp.write_bytes(data)
    try:
        try:
            Image.open(tmp).verify()
        except Exception as exc:
            raise ValueError("Das ist keine Bilddatei.") from exc
        title = Path(filename).stem.replace("_", " ").strip() or "Eigenes Bild"
        meta = new_project(title, {"kind": "upload", "filename": filename, "scan": False})
        return _set_source(meta, tmp)
    finally:
        tmp.unlink(missing_ok=True)


# --- generated images --------------------------------------------------------------------------------


def generator_url(prompt: str, width: int, height: int, seed: int) -> str:
    template = settings_mod.load().get("image_generator_url") or DEFAULT_GENERATOR
    values = {"{prompt}": quote(prompt, safe=""), "{width}": str(width), "{height}": str(height), "{seed}": str(seed)}
    for key, value in values.items():  # plain replace: other braces in the URL stay untouched
        template = template.replace(key, value)
    return template


def gen_size(fmt: str) -> tuple[int, int]:
    """Size to request from the generator: long side GEN_LONG in the mat's aspect, multiples of 64."""
    _, w, h = FORMATS[fmt]
    w_px, h_px = (GEN_LONG, GEN_LONG * h / w) if w >= h else (GEN_LONG * w / h, GEN_LONG)
    return max(64, round(w_px / 64) * 64), max(64, round(h_px / 64) * 64)


async def _fetch_generated(url: str, dest: Path, retries: int = 3) -> Path:
    """GET an image from the generator (slow, rate-limited): waits on 429 and retries 5xx."""
    import asyncio

    for attempt in range(retries + 1):
        await _throttle(url.split("/")[2])
        resp = await client().get(url, timeout=180)
        if resp.status_code in (429, 502, 503) and attempt < retries:
            await asyncio.sleep(float(resp.headers.get("Retry-After") or 5 * (attempt + 1)))
            continue
        if resp.status_code >= 400:
            raise HttpError(resp.status_code, url.split("?")[0][:80], "Bildgenerator")
        if not resp.headers.get("content-type", "").startswith("image/"):
            raise ValueError("Der Bildgenerator hat kein Bild geliefert.")
        dest.write_bytes(resp.content)
        return dest
    raise HttpError(429, url[:80], "Bildgenerator überlastet")


async def generate(title: str, prompt: str, fmt: str, *, variants: int = 3, setting: str = "", style: str = "",
                   progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
    """Create a project with ``variants`` generated candidates (different seeds) of ``prompt``."""
    w, h = gen_size(fmt)
    meta = new_project(title, {"kind": "generated", "prompt": prompt, "setting": setting, "style": style, "scan": False})
    meta["format"] = fmt
    d = _dir(meta["id"]) / "candidates"
    d.mkdir(parents=True, exist_ok=True)
    errors = []
    for n in range(max(1, min(variants, 4))):
        seed = random.randint(1, 2**31 - 1)
        if progress:
            progress(f"Erzeuge Variante {n + 1} von {variants} …")
        raw = d / f"raw-{n}"
        try:
            await _fetch_generated(generator_url(prompt, w, h, seed), raw)
            name, size = _store_image(meta["id"], raw, f"candidates/{n + 1}")
            meta["candidates"].append({"file": name, "size": size, "seed": seed})
        except Exception as exc:  # keep the variants that worked
            errors.append(str(exc))
        finally:
            raw.unlink(missing_ok=True)
        _save(meta)
    if not meta["candidates"]:
        delete(meta["id"])
        raise RuntimeError("Der Bildgenerator hat kein Bild geliefert: " + "; ".join(errors[:2]))
    meta["errors"] = errors
    return _save(meta)


def choose(pid: str, n: int) -> dict[str, Any]:
    meta = load(pid)
    cands = meta.get("candidates") or []
    if not 0 <= n < len(cands):
        raise ValueError("Diese Variante gibt es nicht.")
    return _set_source(meta, _dir(pid) / cands[n]["file"], seed=cands[n]["seed"], chosen=n)


# --- rendering -------------------------------------------------------------------------------------


def crop_box(size: tuple[int, int], aspect: float, cx: float = 0.5, cy: float = 0.5, zoom: float = 1.0) -> tuple[int, int, int, int]:
    """Largest window of ``aspect`` inside ``size``, shrunk by ``zoom`` and centred on (cx, cy) as far
    as it stays inside. The GUI preview uses the same maths."""
    w, h = size
    win_w, win_h = (h * aspect, h) if w / h > aspect else (w, w / aspect)
    zoom = max(1.0, min(zoom, 8.0))
    win_w, win_h = win_w / zoom, win_h / zoom
    x0 = min(max(cx * w - win_w / 2, 0), w - win_w)
    y0 = min(max(cy * h - win_h / 2, 0), h - win_h)
    return round(x0), round(y0), round(x0 + win_w), round(y0 + win_h)


def default_crop(meta: dict[str, Any], aspect: float) -> dict[str, float]:
    """Initial crop: centre; for MPC scans the art box of the frame."""
    box = meta["source"].get("art_box")
    if not box:
        return {"cx": 0.5, "cy": 0.5, "zoom": 1.0}
    w, h = meta["source"]["size"]
    bw, bh = (box[2] - box[0]) * w, (box[3] - box[1]) * h
    full = crop_box((w, h), aspect)
    win_w = min(bw, bh * aspect)
    return {"cx": (box[0] + box[2]) / 2, "cy": (box[1] + box[3]) / 2, "zoom": round((full[2] - full[0]) / win_w, 3)}


async def _esrgan(img: Image.Image, work: Path, cfg: dict[str, Any]) -> Image.Image:
    """One Real-ESRGAN ×4 pass (raises FileNotFoundError / UpscaleError)."""
    raw = work / f"upscale-in-{uuid.uuid4().hex[:6]}.png"
    img.convert("RGB").save(raw, compress_level=1)
    try:
        out = await proxy._upscale(raw, cfg, timeout=1800)
        with Image.open(out) as big:
            big.load()
            return big.convert("RGB")
    finally:
        raw.unlink(missing_ok=True)
        for f in work.glob("upscale-in-*-x4-*.png"):
            f.unlink(missing_ok=True)


def _resize(img: Image.Image, size: tuple[int, int], sharpen: bool) -> Image.Image:
    out = img.convert("RGB") if img.size == size else img.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    if sharpen:
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=45, threshold=2))
    return out


async def upscale_to(img: Image.Image, size: tuple[int, int], work: Path, cfg: dict[str, Any], *, ai: bool, scan: bool,
                     warnings: list[str], say: Progress, max_passes: int = 1) -> tuple[Image.Image, int]:  # fmt: skip
    """Bring ``img`` to exactly ``size``. With ``ai``: descreen scans, Real-ESRGAN ×4 – and, if allowed
    (``max_passes=2``) and the factor is above ~4.5, a second pass that starts from a quarter of the target
    (so it ends on the print size instead of overshooting) – then Lanczos + light sharpening.
    One pass often looks more natural; two are sharper but can look artificial. Returns image and passes."""
    factor = size[0] / img.width
    if not ai or factor <= 1.15:
        return _resize(img, size, sharpen=factor > 1.15), 0
    passes = 2 if max_passes >= 2 and factor > 4.5 else 1
    if scan and cfg.get("descreen", "normal") != "off":
        img = imaging.descreen(img, cfg.get("descreen", "normal"))
    done = 0
    try:
        say(f"KI-Hochskalierung, Durchgang 1 von {passes} (Real-ESRGAN ×4) …")
        img = await _esrgan(img, work, cfg)
        done = 1
        if passes == 2:
            quarter = (max(1, round(size[0] / 4)), max(1, round(size[1] / 4)))
            say(f"KI-Hochskalierung, Durchgang 2 von 2 (auf {size[0]} × {size[1]} px) …")
            img = await _esrgan(img.resize(quarter, Image.Resampling.LANCZOS), work, cfg)
            done = 2
    except FileNotFoundError:
        warnings.append("Real-ESRGAN ist nicht eingerichtet – ohne KI vergrößert (weicher). Einrichtung: Einstellungen → Proxy-Druck.")
    except proxy.UpscaleError as exc:
        warnings.append(f"{exc} – {'nur ein KI-Durchgang' if done else 'ohne KI vergrößert'}.")
    say(f"Skaliere auf {size[0]} × {size[1]} px …")
    return _resize(img, size, sharpen=img.width < size[0] * 0.95), done


async def render(pid: str, *, fmt: str = "playmat", dpi: int = 300, bleed_mm: float = 0, fit: str = "fill",
                 crop: dict[str, float] | None = None, upscale: bool = True, filetype: str = "png", passes: int = 1,
                 progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
    """Render the project's source to the print file (+ preview.jpg) and return the project."""
    size = check_size(fmt, dpi, bleed_mm)
    if filetype not in ("png", "jpg"):
        raise ValueError("Dateiformat: png oder jpg")
    meta = load(pid)
    aspect = size[0] / size[1]
    crop = crop or default_crop(meta, aspect)
    cfg = settings_mod.load()
    scan = bool(meta["source"].get("scan"))
    warnings: list[str] = []
    work = _dir(pid)
    say = progress or (lambda _t: None)

    with Image.open(file(pid, "source")) as src:
        src.load()
        src = src.convert("RGB")
    if fit == "fit":
        # whole image, centred; the rest is the same image enlarged, blurred and darkened
        scale = min(size[0] / src.width, size[1] / src.height)
        fg_size = (round(src.width * scale), round(src.height * scale))
        fg, used = await upscale_to(src, fg_size, work, cfg, ai=upscale, scan=scan, warnings=warnings, say=say, max_passes=passes)
        say("Setze das Bild zusammen …")
        bg_scale = max(size[0] / src.width, size[1] / src.height)
        small = src.resize((max(1, round(src.width * bg_scale / 16)), max(1, round(src.height * bg_scale / 16))), Image.Resampling.BILINEAR)
        bg = ImageEnhance.Brightness(small.filter(ImageFilter.GaussianBlur(4))).enhance(0.55)
        bg = bg.resize((round(src.width * bg_scale), round(src.height * bg_scale)), Image.Resampling.BICUBIC)
        left, top = (bg.width - size[0]) // 2, (bg.height - size[1]) // 2
        out = bg.crop((left, top, left + size[0], top + size[1]))
        del bg
        out.paste(fg, ((size[0] - fg_size[0]) // 2, (size[1] - fg_size[1]) // 2))
        del fg
        region_size = (src.width, src.height)
    else:
        box = crop_box((src.width, src.height), aspect, crop.get("cx", 0.5), crop.get("cy", 0.5), crop.get("zoom", 1.0))
        region = src.crop(box)
        region_size = region.size
        scale = size[0] / region.width
        out, used = await upscale_to(region, size, work, cfg, ai=upscale, scan=scan, warnings=warnings, say=say, max_passes=passes)
    limit = {0: 2.5, 1: 6, 2: 24}[used]
    if scale > limit:
        warnings.append(f"Das Motiv ist klein für {dpi} DPI (Faktor {de_num(scale, 1)}) – die Datei hat {dpi} DPI, wirkt aber weich. "
                        "Schärfer: MPC-Scan, generiertes Bild oder eigenes Bild in hoher Auflösung.")  # fmt: skip

    for old in [*work.glob("deskmat-*.png"), *work.glob("deskmat-*.jpg")]:
        old.unlink()
    name = f"deskmat-{size[0]}x{size[1]}-{dpi}dpi.{filetype}"
    say(f"Speichere {filetype.upper()} ({de_num(size[0] * size[1] / 1e6, 0)} Megapixel) …")
    if filetype == "jpg":
        out.save(work / name, quality=95, subsampling=0, dpi=(dpi, dpi))
    else:
        out.save(work / name, compress_level=6 if size[0] * size[1] < 60e6 else 3, dpi=(dpi, dpi))
    prev_w = 1200
    out.resize((prev_w, max(1, round(prev_w * size[1] / size[0]))), Image.Resampling.LANCZOS, reducing_gap=3.0).save(work / "preview.jpg", quality=86)
    _, w_mm, h_mm = FORMATS[fmt]
    meta["render"] = {"format": fmt, "dpi": dpi, "bleed_mm": bleed_mm, "fit": fit, "crop": crop, "upscale": upscale,
                      "filetype": filetype, "passes": passes}  # fmt: skip
    meta["result"] = {"file": name, "preview": "preview.jpg", "size": list(size), "dpi": dpi, "bleed_mm": bleed_mm,
                      "print_mm": [w_mm + 2 * bleed_mm, h_mm + 2 * bleed_mm], "format_label": FORMATS[fmt][0],
                      "source_px": list(region_size), "factor": round(scale, 2), "ai_passes": used, "ai_upscaled": used > 0,
                      "warnings": warnings, "bytes": (work / name).stat().st_size, "created": _now()}  # fmt: skip
    return _save(meta)


# --- checking before ordering: detail comparison and a test print at real size ----------------------

COMPARE_PX = 640  # side of a comparison tile in print pixels (≈ 5.4 cm at 300 DPI)
TEST_MM = {"A4": (270, 190), "Letter": (259, 190)}  # printed region on a landscape sheet, leaves printer margins
PAPER_MM = {"A4": (297, 210), "Letter": (279.4, 215.9)}


def _mapping(meta: dict[str, Any], src_size: tuple[int, int]) -> tuple[tuple[int, int], float, float, float, tuple[float, float, float, float]]:
    """How print pixels map to source pixels for the last render settings:
    ``(print size, origin_x, origin_y, factor, area)`` with ``src = origin + print / factor``; ``area`` is the
    part of the print that shows the source (all of it for fill, the centred image for fit)."""
    r = meta.get("render") or {}
    size = target_size(r.get("format", "playmat"), r.get("dpi", 300), r.get("bleed_mm", 0))
    w, h = src_size
    if r.get("fit") == "fit":
        f = min(size[0] / w, size[1] / h)
        off = ((size[0] - w * f) / 2, (size[1] - h * f) / 2)
        return size, -off[0] / f, -off[1] / f, f, (off[0], off[1], off[0] + w * f, off[1] + h * f)
    crop = r.get("crop") or default_crop(meta, size[0] / size[1])
    x0, y0, x1, _ = crop_box(src_size, size[0] / size[1], crop.get("cx", 0.5), crop.get("cy", 0.5), crop.get("zoom", 1.0))
    return size, x0, y0, size[0] / (x1 - x0), (0, 0, size[0], size[1])


def _tile_box(point: tuple[float, float], tile: tuple[int, int], area: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """A ``tile``-sized box centred on ``point`` (fractions of the print), kept inside ``area``."""
    ax0, ay0, ax1, ay1 = area
    tw, th = min(tile[0], int(ax1 - ax0)), min(tile[1], int(ay1 - ay0))
    x0 = min(max(point[0] - tw / 2, ax0), ax1 - tw)
    y0 = min(max(point[1] - th / 2, ay0), ay1 - th)
    return round(x0), round(y0), round(x0) + tw, round(y0) + th


async def compare(pid: str, *, x: float = 0.5, y: float = 0.5, progress: Progress | None = None) -> dict[str, Any]:
    """Render one detail of the print (``COMPARE_PX`` square around the point x/y, fractions of the print)
    without AI, with one and with two Real-ESRGAN passes – exactly as the full render would, but only for
    that piece. Files: ``compare/<passes>.png``."""
    meta = load(pid)
    if not meta.get("render"):
        raise ValueError("Erst die Deskmat erstellen – der Vergleich nutzt deren Format und Zuschnitt.")
    say = progress or (lambda _t: None)
    cfg = settings_mod.load()
    work = _dir(pid)
    with Image.open(file(pid, "source")) as src:
        src.load()
        src = src.convert("RGB")
    size, ox, oy, f, area = _mapping(meta, src.size)
    box = _tile_box((x * size[0], y * size[1]), (COMPARE_PX, COMPARE_PX), area)
    margin = 8  # source pixels of context around the tile, so the AI sees the neighbourhood
    sx0, sy0 = ox + box[0] / f, oy + box[1] / f
    sx1, sy1 = ox + box[2] / f, oy + box[3] / f
    cx0, cy0 = max(0, int(sx0) - margin), max(0, int(sy0) - margin)
    cx1, cy1 = min(src.width, int(sx1 + 1) + margin), min(src.height, int(sy1 + 1) + margin)
    piece = src.crop((cx0, cy0, cx1, cy1))
    big = (round(piece.width * f), round(piece.height * f))
    inner = (round((sx0 - cx0) * f), round((sy0 - cy0) * f))
    tw, th = box[2] - box[0], box[3] - box[1]
    out_dir = work / "compare"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    tiles, warnings = [], []
    scan = bool(meta["source"].get("scan"))
    upscaler = bool(proxy.find_upscaler(cfg))
    for passes in (0, 1, 2):
        if passes and not upscaler:
            continue
        if passes == 2 and f <= 4.5:
            continue  # a second pass is only used for large factors
        say({0: "Ohne KI …", 1: "Mit einem KI-Durchgang …", 2: "Mit zwei KI-Durchgängen …"}[passes])
        img, used = await upscale_to(piece, big, work, cfg, ai=passes > 0, scan=scan, warnings=warnings,
                                     say=lambda _t: None, max_passes=max(passes, 1))  # fmt: skip
        if passes and used != passes:
            continue
        tile = img.crop((inner[0], inner[1], inner[0] + tw, inner[1] + th))
        tile.save(out_dir / f"{passes}.png")
        tiles.append({"passes": passes, "file": f"compare/{passes}.png"})
    meta["compare"] = {"tiles": tiles, "point": [x, y], "box": list(box), "print_size": list(size), "warnings": warnings,
                       "tile_mm": round(tw / meta["render"]["dpi"] * 25.4), "factor": round(f, 2), "created": _now()}  # fmt: skip
    return _save(meta)


def compare_file(pid: str, passes: int) -> Path:
    path = _dir(pid) / "compare" / f"{int(passes)}.png"
    if not path.is_file():
        raise FileNotFoundError("Vergleich fehlt")
    return path


def _note_font(px: int) -> Any:
    """A readable font for the note on the test print (system fonts with umlauts, else Pillow's default)."""
    from PIL import ImageFont

    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "Helvetica.ttc", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def testprint(pid: str, *, x: float = 0.5, y: float = 0.5, paper: str = "A4") -> Path:
    """PDF with a piece of the finished print at real size on one landscape sheet (print it at 100 % /
    "actual size"), centred on x/y (fractions of the print), with crop marks and a short note."""
    from PIL import ImageDraw

    meta = load(pid)
    res = meta.get("result")
    if not res:
        raise ValueError("Erst die Deskmat erstellen.")
    paper = paper if paper in TEST_MM else "A4"
    dpi = res["dpi"]
    px = lambda mm: round(mm / 25.4 * dpi)  # noqa: E731
    region = (min(px(TEST_MM[paper][0]), res["size"][0]), min(px(TEST_MM[paper][1]), res["size"][1]))
    box = _tile_box((x * res["size"][0], y * res["size"][1]), region, (0, 0, *res["size"]))
    with Image.open(file(pid, "result")) as im:
        piece = im.crop(box).convert("RGB")
    sheet = Image.new("RGB", (px(PAPER_MM[paper][0]), px(PAPER_MM[paper][1])), "white")
    left, top = (sheet.width - piece.width) // 2, (sheet.height - piece.height) // 2
    sheet.paste(piece, (left, top))
    draw = ImageDraw.Draw(sheet)
    mark, gap = px(5), px(1)
    for cx, cy, dx, dy in ((left, top, -1, -1), (left + piece.width, top, 1, -1), (left, top + piece.height, -1, 1),
                           (left + piece.width, top + piece.height, 1, 1)):  # fmt: skip
        draw.line([(cx + dx * gap, cy), (cx + dx * (gap + mark), cy)], fill="black", width=max(1, dpi // 150))
        draw.line([(cx, cy + dy * gap), (cx, cy + dy * (gap + mark))], fill="black", width=max(1, dpi // 150))
    note = (f"Probedruck \"{meta['title']}\" - {dpi} DPI - Ausschnitt {de_num(piece.width / dpi * 2.54, 1)} x {de_num(piece.height / dpi * 2.54, 1)} cm"
            " - mit 100 % (Tatsächliche Größe) drucken und aus 50-60 cm ansehen")  # fmt: skip
    draw.text((left + gap * 2, top + piece.height + gap + mark // 3), note, fill="black", font=_note_font(round(dpi * 9 / 72)))
    out = _dir(pid) / f"probedruck-{paper.lower()}.pdf"
    sheet.save(out, "PDF", resolution=dpi)
    return out
