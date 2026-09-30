"""Deskmat / playmat studio: turn a card artwork, a generated image or an upload into a print file of
about 4K (long side 4096 px by default) in the mat's aspect ratio.

A project lives in ``deskmats/<id>/`` (``MTG_DESKMAT_DIR``)::

    meta.json            title, source info, last render settings and result
    source.<ext>         the chosen motif (original resolution)
    candidates/<n>.<ext> generated variants to choose from
    deskmat-<W>x<H>.png  the result, preview.jpg a small copy for the GUI

Rendering: crop (fill: a window in the target aspect, centre + zoom) or fit (whole image on a blurred,
darkened extension of itself) → descreen for printed card scans → Real-ESRGAN ×4 (the upscaler of the
proxy printing, opt-out) → Lanczos to the exact size + light sharpening.
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
from . import settings as settings_mod
from .http import HttpError, _throttle, client, download

DESKMAT_DIR = Path(os.environ.get("MTG_DESKMAT_DIR", storage.PROJECT_ROOT / "deskmats"))
Image.MAX_IMAGE_PIXELS = 200_000_000  # upscaled intermediates are big but trusted (our own files)

# key -> (label, width mm, height mm)
FORMATS: dict[str, tuple[str, int, int]] = {
    "playmat": ("Playmat 61 × 35,5 cm (Standard)", 610, 355),
    "deskmat-80x30": ("Deskmat 80 × 30 cm", 800, 300),
    "deskmat-90x40": ("Deskmat 90 × 40 cm", 900, 400),
    "deskmat-120x60": ("Deskmat 120 × 60 cm", 1200, 600),
    "screen-16x9": ("16:9 (Bildschirm / Hintergrund)", 1600, 900),
}
SIZES = [3840, 4096, 5120]  # long side in px; 4096 = "4K"
DEFAULT_LONG = 4096
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


def target_size(fmt: str, long_px: int = DEFAULT_LONG) -> tuple[int, int]:
    _, w, h = FORMATS[fmt]
    return (long_px, round(long_px * h / w)) if w >= h else (round(long_px * w / h), long_px)


def dpi(fmt: str, size: tuple[int, int]) -> int:
    return round(size[0] / (FORMATS[fmt][1] / 25.4))


def formats() -> dict[str, Any]:
    cfg = settings_mod.load()
    exe = proxy.find_upscaler(cfg)
    return {
        "formats": [{"key": k, "label": v[0], "mm": [v[1], v[2]], "aspect": v[1] / v[2],
                     "sizes": {str(s): list(target_size(k, s)) for s in SIZES}} for k, v in FORMATS.items()],
        "sizes": SIZES, "default_size": DEFAULT_LONG,
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
    d = _dir(meta["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
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
    """Size to request from the generator: long side GEN_LONG, multiples of 64."""
    w, h = target_size(fmt, GEN_LONG)
    return max(64, round(w / 64) * 64), max(64, round(h / 64) * 64)


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


async def _ai_upscale(img: Image.Image, work: Path, cfg: dict[str, Any], scan: bool, warnings: list[str]) -> tuple[Image.Image, bool]:
    """Real-ESRGAN ×4 (after descreening scans); on any problem the image stays as it is plus a warning."""
    if scan and cfg.get("descreen", "normal") != "off":
        img = imaging.descreen(img, cfg.get("descreen", "normal"))
    raw = work / f"upscale-in-{uuid.uuid4().hex[:6]}.png"
    img.convert("RGB").save(raw)
    try:
        out = await proxy._upscale(raw, cfg)
        with Image.open(out) as big:
            big.load()
            return big.convert("RGB"), True
    except FileNotFoundError:
        warnings.append("Real-ESRGAN ist nicht eingerichtet – ohne KI hochskaliert (weicher). Einrichtung: Einstellungen → Proxy-Druck.")
    except proxy.UpscaleError as exc:
        warnings.append(f"{exc} – ohne KI hochskaliert.")
    finally:
        raw.unlink(missing_ok=True)
        for f in work.glob("upscale-in-*-x4-*.png"):
            f.unlink(missing_ok=True)
    return img, False


def _resize(img: Image.Image, size: tuple[int, int], sharpen: bool) -> Image.Image:
    out = img.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    if sharpen:
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=45, threshold=2))
    return out


async def render(pid: str, *, fmt: str = "playmat", long_px: int = DEFAULT_LONG, fit: str = "fill",
                 crop: dict[str, float] | None = None, upscale: bool = True, progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
    """Render the project's source to ``deskmat-<W>x<H>.png`` (+ preview.jpg) and return the project."""
    if fmt not in FORMATS:
        raise ValueError(f"Unbekanntes Format: {fmt}")
    if long_px not in SIZES:
        raise ValueError(f"Auflösung muss eine von {SIZES} sein")
    meta = load(pid)
    size = target_size(fmt, long_px)
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
        fg, used_ai = src, False
        if upscale and scale > 1.15:
            say("KI-Hochskalierung (Real-ESRGAN ×4) …")
            fg, used_ai = await _ai_upscale(src, work, cfg, scan, warnings)
        say("Setze das Bild zusammen …")
        bg_scale = max(size[0] / src.width, size[1] / src.height)
        bg = src.resize((max(1, round(src.width * bg_scale / 8)), max(1, round(src.height * bg_scale / 8))), Image.Resampling.BILINEAR)
        bg = bg.filter(ImageFilter.GaussianBlur(6)).resize((round(src.width * bg_scale), round(src.height * bg_scale)), Image.Resampling.BICUBIC)
        bg = ImageEnhance.Brightness(bg).enhance(0.55)
        canvas = bg.crop(((bg.width - size[0]) // 2, (bg.height - size[1]) // 2, (bg.width - size[0]) // 2 + size[0], (bg.height - size[1]) // 2 + size[1]))
        canvas.paste(_resize(fg, fg_size, sharpen=scale > 1.15), ((size[0] - fg_size[0]) // 2, (size[1] - fg_size[1]) // 2))
        region_size = (src.width, src.height)
        out = canvas
    else:
        box = crop_box((src.width, src.height), aspect, crop.get("cx", 0.5), crop.get("cy", 0.5), crop.get("zoom", 1.0))
        region = src.crop(box)
        region_size = region.size
        scale = size[0] / region.width
        used_ai = False
        if upscale and scale > 1.15:
            say("KI-Hochskalierung (Real-ESRGAN ×4) …")
            region, used_ai = await _ai_upscale(region, work, cfg, scan, warnings)
        say(f"Skaliere auf {size[0]} × {size[1]} px …")
        out = _resize(region, size, sharpen=scale > 1.15)
    if scale > (6 if used_ai else 2.5):
        warnings.append(f"Das Motiv ist klein für dieses Format (Faktor {scale:.1f}) – es wird weich wirken. "
                        "Schärfer: MPC-Scan, generiertes Bild oder eigenes Bild in hoher Auflösung.")  # fmt: skip

    for old in work.glob("deskmat-*.png"):
        old.unlink()
    name = f"deskmat-{size[0]}x{size[1]}.png"
    say("Speichere PNG …")
    out.save(work / name, dpi=(dpi(fmt, size), dpi(fmt, size)))
    preview = out.copy()
    preview.thumbnail((1200, 1200))
    preview.save(work / "preview.jpg", quality=86)
    meta["render"] = {"format": fmt, "long_px": long_px, "fit": fit, "crop": crop, "upscale": upscale}
    meta["result"] = {"file": name, "preview": "preview.jpg", "size": list(size), "dpi": dpi(fmt, size),
                      "format_label": FORMATS[fmt][0], "source_px": list(region_size), "factor": round(scale, 2),
                      "ai_upscaled": used_ai, "warnings": warnings, "bytes": (work / name).stat().st_size, "created": _now()}  # fmt: skip
    return _save(meta)
