"""Proxy printing studio, integrated with MPC Autofill (https://github.com/chilli-axe/mpc-autofill).

Pipeline (everything except the MakePlayingCards upload happens here, cross-platform):

1. ``plan()``        – which image per card face: the user's choice (``selection.json``), else the
                       best MPC Autofill community scan (if a search server is configured), else
                       Scryfall's scan of the card. Double-faced cards get their back face.
2. ``alternatives()``– image options for one card (MPC Autofill scans + all Scryfall printings)
                       for the picker in the GUI; ``choose()`` stores the pick.
3. ``prepare()``     – downloads the chosen images in parallel into a shared cache, gives
                       Scryfall scans a bleed edge, adds a cardback and writes the order XML with
                       local files only. The desktop tool then only has to upload.
4. ``export_pdf()``  – home-printing PDF (3 x 3 per page, cut marks) – no desktop tool needed.
5. ``autofill_command()`` / ``launch_autofill()`` – run the desktop tool on the order folder
                       (upload to MakePlayingCards or its own PDF export).

Order XML (format of the mpcfill.com frontend / desktop tool)::

    <order>
      <details><quantity>100</quantity><stock>(S30) Standard Smooth</stock><foil>false</foil></details>
      <fronts><card><id>C:\\…\\sol-ring.jpg</id><sourceType>Local File</sourceType><slots>0,1</slots>
                    <name>sol-ring.jpg</name><query>sol ring</query></card>…</fronts>
      <backs>…back faces of double-faced cards…</backs>
      <cardback>C:\\…\\cardback.jpg</cardback>
    </order>
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from . import carddb, imaging, scryfall
from . import settings as settings_mod
from .cards import resolve
from .http import download, get_json, post_json
from .storage import PROJECT_ROOT

PROXIES_DIR = Path(os.environ.get("MTG_PROXIES_DIR", PROJECT_ROOT / "proxies"))

STOCKS = ["(S27) Smooth", "(S30) Standard Smooth", "(S33) Superior Smooth", "(M31) Linen", "(P10) Plastic"]
MPC_BRACKETS = [18, 36, 55, 72, 90, 108, 126, 144, 162, 180, 198, 216, 234, 396, 504, 612]
DFC_LAYOUTS = {"transform", "modal_dfc", "reversible_card", "double_faced_token"}
EDITOR_SEARCH_MAX_QUERIES = 300
MPC_DPI = 800  # resolution requested from the MPC Autofill image CDN
LOCAL_FILE = "Local File"
PARALLEL_DOWNLOADS = 6

Progress = Callable[[int, int, str], Awaitable[None] | None]


def mpc_bracket(quantity: int) -> int:
    return next((b for b in MPC_BRACKETS if b >= quantity), MPC_BRACKETS[-1])


def process_query(name: str) -> str:
    """Same normalisation as the MPC Autofill frontend (lowercase, no punctuation)."""
    q = re.sub(r"[~`!@#$%^&*(){}\[\];:\"'’<,.>?/\\|_+=]", "", name.lower().strip())
    return re.sub(r"\s+", " ", q).strip()


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip() or "card"


def order_dir(slug: str) -> Path:
    return PROXIES_DIR / slug


def image_cache() -> Path:
    return carddb.DATA_DIR / "images"


# --- MPC Autofill search server + image CDN ------------------------------------------------------


class MpcFill:
    """Client for an MPC Autofill backend (the server configured on mpcfill.com) and its image CDN."""

    def __init__(self, server: str, cdn: str):
        server = server.strip().rstrip("/")
        self.base = server if server.startswith("http") else f"https://{server}"
        self.cdn = cdn.rstrip("/")
        self._settings: dict[str, Any] | None = None

    async def search_settings(self) -> dict[str, Any]:
        if self._settings is None:
            sources = (await get_json(f"{self.base}/2/sources/", ttl=24 * 3600)).get("results") or {}
            ordered = sorted(sources.values(), key=lambda s: (s.get("ordinal", 0), s.get("pk", 0)))
            self._settings = {
                "searchTypeSettings": {"fuzzySearch": False, "filterCardbacks": False},
                "sourceSettings": {"sources": [[int(s["pk"]), True] for s in ordered if "pk" in s]},
                "filterSettings": {
                    "minimumDPI": 0, "maximumDPI": 1500, "maximumSize": 30,
                    "languages": [], "includesTags": [], "excludesTags": ["NSFW"],
                },  # fmt: skip
            }
        return self._settings

    async def search(self, names: list[str]) -> dict[str, list[str]]:
        """Card face name -> image identifiers (best first)."""
        settings = await self.search_settings()
        out: dict[str, list[str]] = {}
        unique = list(dict.fromkeys(names))
        for i in range(0, len(unique), EDITOR_SEARCH_MAX_QUERIES):
            chunk = unique[i : i + EDITOR_SEARCH_MAX_QUERIES]
            body = {
                "searchSettings": settings,
                "queries": {name: {"query": process_query(name), "cardType": "CARD"} for name in chunk},
            }
            results = (await post_json(f"{self.base}/3/editorSearch/", body, ttl=24 * 3600)).get("results") or {}
            out.update({name: results.get(name) or [] for name in chunk})
        return out

    async def cards(self, identifiers: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        ids = list(dict.fromkeys(identifiers))
        for i in range(0, len(ids), 1000):
            data = await post_json(f"{self.base}/2/cards/", {"cardIdentifiers": ids[i : i + 1000]}, ttl=24 * 3600)
            out.update(data.get("results") or {})
        return out

    async def cardbacks(self) -> list[str]:
        settings = await self.search_settings()
        data = await post_json(f"{self.base}/2/cardbacks/", {"searchSettings": settings}, ttl=24 * 3600)
        return data.get("cardbacks") or []

    def thumb(self, ident: str) -> str:
        return f"{self.cdn}/images/google_drive/small/{ident}.jpg"

    def full(self, ident: str, dpi: int = MPC_DPI) -> str:
        return f"{self.cdn}/images/google_drive/full/{ident}.jpg?dpi={dpi}"

    def option(self, ident: str, details: dict[str, Any], face: str) -> dict[str, Any]:
        return {
            "origin": "mpcfill",
            "id": ident,
            "thumb": details.get("smallThumbnailUrl") or self.thumb(ident),
            "full": self.full(ident),
            "label": details.get("sourceName") or details.get("source") or "MPC Autofill",
            "dpi": details.get("dpi"),
            "name": details.get("name") or face,
        }


def _mpc_client(cfg: dict[str, Any], source: str) -> MpcFill | None:
    if source == "scryfall" or not cfg.get("mpcfill_server"):
        return None
    return MpcFill(cfg["mpcfill_server"], cfg.get("mpcfill_cdn") or settings_mod.DEFAULTS["mpcfill_cdn"])


# --- Scryfall images ------------------------------------------------------------------------------


def _png_url(url: str | None) -> str | None:
    """Scryfall 'normal' JPG -> full resolution PNG scan of the same printing."""
    if not url:
        return None
    return url.replace("/normal/", "/png/").replace("/large/", "/png/").split("?")[0].rsplit(".", 1)[0] + ".png"


def _scryfall_option(card: dict[str, Any], side: str, face: str) -> dict[str, Any] | None:
    thumb = card.get("image") if side == "front" else card.get("image_back")
    if not thumb:
        return None
    return {
        "origin": "scryfall",
        "id": _png_url(thumb),
        "thumb": thumb,
        "full": _png_url(thumb),
        "label": f"Scryfall{' · ' + card['set_name'] if card.get('set_name') else ''}",
        "dpi": 300,
        "name": face,
    }


# --- plan ---------------------------------------------------------------------------------------


def _selection_path(slug: str) -> Path:
    return order_dir(slug) / "selection.json"


def load_selection(slug: str) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_selection_path(slug).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def choose(slug: str, face: str, option: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Remember the image for one card face (``None`` = back to automatic choice)."""
    sel = load_selection(slug)
    if option:
        sel[face] = {k: option.get(k) for k in ("origin", "id", "thumb", "full", "label", "dpi", "name")}
    else:
        sel.pop(face, None)
    path = _selection_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sel, indent=2, ensure_ascii=False), "utf-8")
    return sel


def _slot_names(deck: dict[str, Any]) -> list[tuple[str, bool]]:
    slots = [(c, True) for c in deck.get("commanders", [])]
    for c in sorted(deck.get("cards", []), key=lambda c: c["name"]):
        slots += [(c["name"], False)] * int(c.get("qty", 1))
    return slots


async def plan(deck: dict[str, Any], *, source: str = "auto") -> dict[str, Any]:
    """Decide the image for every card face (no downloads).

    source: 'auto' (own choices > MPC Autofill > Scryfall), 'mpcfill' (requires a server) or 'scryfall'.
    """
    cfg = settings_mod.load()
    if source == "mpcfill" and not cfg.get("mpcfill_server"):
        raise ValueError("Kein MPC-Autofill-Server eingestellt (Einstellungen → MPC-Autofill-Server).")
    slug = deck["slug"]
    slots = _slot_names(deck)
    unique = list(dict.fromkeys(n for n, _ in slots))
    card_data, _, not_found = await resolve(unique)

    faces: dict[str, dict[str, Any]] = {}
    for name in unique:
        c = card_data.get(name, {})
        parts = name.split(" // ")
        is_dfc = c.get("layout") in DFC_LAYOUTS or bool(c.get("image_back"))
        faces[name] = {"front": parts[0], "back": parts[1] if is_dfc and len(parts) > 1 else None}

    chosen: dict[str, dict[str, Any]] = {}
    hit_counts: dict[str, int] = {}
    for name, f in faces.items():
        for side in ("front", "back"):
            if f[side] and (opt := _scryfall_option(card_data.get(name, {}), side, f[side])):
                chosen[f[side]] = opt

    warnings: list[str] = []
    mpc = _mpc_client(cfg, source)
    if mpc:
        try:
            face_names = [f[s] for f in faces.values() for s in ("front", "back") if f[s]]
            hits = await mpc.search(face_names)
            hit_counts = {face: len(ids) for face, ids in hits.items()}
            best = {face: ids[0] for face, ids in hits.items() if ids}
            details = await mpc.cards(list(best.values()))
            for face, ident in best.items():
                chosen[face] = mpc.option(ident, details.get(ident, {}), face)
        except Exception as exc:  # wrong URL / server down -> Scryfall
            warnings.append(f"MPC-Autofill-Server nicht nutzbar ({exc}) – verwende Scryfall-Bilder.")

    for face, opt in load_selection(slug).items():
        if source == "scryfall" and opt.get("origin") == "mpcfill":
            continue
        opt["custom"] = True
        chosen[face] = opt

    counts = {n: 0 for n in unique}
    for n, _ in slots:
        counts[n] += 1
    cards = []
    for name in unique:
        f = faces[name]
        entry = {"name": name, "qty": counts[name], "commander": name in deck.get("commanders", [])}
        for side in ("front", "back"):
            face = f[side]
            entry[side] = {"face": face, "image": chosen.get(face), "mpc_hits": hit_counts.get(face, 0)} if face else None
        cards.append(entry)
    missing = sorted(set(not_found) | {c["name"] for c in cards if not c["front"]["image"]})
    return {
        "slug": slug,
        "quantity": len(slots),
        "mpc_bracket": mpc_bracket(len(slots)),
        "server": bool(mpc),
        "cards": cards,
        "missing": missing,
        "warnings": warnings,
    }


async def alternatives(deck: dict[str, Any], card_name: str, side: str = "front", limit: int = 40) -> list[dict[str, Any]]:
    """All image options for one card face: MPC Autofill scans and every Scryfall printing."""
    cfg = settings_mod.load()
    parts = card_name.split(" // ")
    face = parts[1] if side == "back" and len(parts) > 1 else parts[0]
    options: list[dict[str, Any]] = []
    mpc = _mpc_client(cfg, "auto")
    if mpc:
        try:
            ids = (await mpc.search([face])).get(face, [])[:limit]
            details = await mpc.cards(ids)
            options += [mpc.option(i, details.get(i, {}), face) for i in ids]
        except Exception:
            pass
    prints = await scryfall.search(f'!"{parts[0]}" game:paper', order="released", unique="prints", max_results=limit)
    for raw in prints["cards"]:
        c = scryfall.compact(raw)
        c["set_name"] = raw.get("set_name")
        if opt := _scryfall_option(c, side, face):
            options.append(opt)
    return options


# --- prepare: download, process, write XML ----------------------------------------------------


def _cache_file(option: dict[str, Any]) -> Path:
    key = hashlib.sha1(str(option["id"]).encode()).hexdigest()[:16]
    if option["origin"] == "mpcfill":
        return image_cache() / "mpcfill" / f"{safe_filename(str(option['id']))}.jpg"
    return image_cache() / "scryfall" / f"{key}.png"


# --- optional AI upscaling (Real-ESRGAN, opt-in) ------------------------------------------------

UPSCALE_DPI = 600  # target resolution of upscaled Scryfall scans
_GPU_ERRORS = ("vkCreateInstance failed", "invalid gpu device", "vkCreateDevice failed", "no vulkan")


class UpscaleError(RuntimeError):
    pass
_upscale_lock = threading.Lock()  # one GPU job at a time (works across event loops/threads)


def find_upscaler(cfg: dict[str, Any] | None = None) -> Path | None:
    """realesrgan-ncnn-vulkan from the settings, else anywhere below tools/."""
    cfg = cfg or settings_mod.load()
    if cfg.get("upscaler_path"):
        p = Path(cfg["upscaler_path"]).expanduser()
        return p if p.is_file() else None
    for pattern in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan", "realesrgan*.py"):
        for candidate in sorted((PROJECT_ROOT / "tools").rglob(pattern)):
            if candidate.is_file():
                return candidate
    return None


def upscale_models(exe: Path | None) -> list[str]:
    """4x models shipped next to the tool (the official zip has realesrgan-x4plus and -x4plus-anime)."""
    if exe is None or not (exe.parent / "models").is_dir():
        return []
    names = sorted(p.stem for p in (exe.parent / "models").glob("*.param"))
    return [n for n in names if "x4plus" in n and (exe.parent / "models" / f"{n}.bin").exists()]


def upscale_command(exe: Path, src: Path, dst: Path, model: str) -> list[str]:
    cmd = [sys.executable, str(exe)] if exe.suffix == ".py" else [str(exe)]
    # x4plus models only support 4x; the model folder has to be named "models" (checked by the tool)
    return cmd + ["-i", str(src), "-o", str(dst), "-n", model, "-s", "4", "-m", str(exe.parent / "models"), "-f", "png"]


async def _upscale(raw: Path, cfg: dict[str, Any]) -> Path:
    exe = find_upscaler(cfg)
    if exe is None:
        raise FileNotFoundError("Real-ESRGAN nicht gefunden")
    out = raw.with_name(f"{raw.stem}-x4-{cfg['upscale_model']}.png")
    if out.exists() and out.stat().st_size > 0:
        return out
    def run() -> subprocess.CompletedProcess[str]:
        with _upscale_lock:
            return subprocess.run(
                upscale_command(exe, raw, out, cfg["upscale_model"]), capture_output=True, text=True, timeout=600
            )

    proc = await asyncio.to_thread(run)
    if proc.returncode != 0 or not out.exists():
        output = f"{proc.stderr or ''}\n{proc.stdout or ''}"
        if any(e.lower() in output.lower() for e in _GPU_ERRORS):
            raise UpscaleError(
                "Real-ESRGAN findet keine Vulkan-fähige Grafikkarte (vkCreateInstance / invalid gpu device). "
                "Grafiktreiber aktualisieren oder die KI-Hochskalierung deaktivieren."
            )
        detail = output.strip().splitlines()[-1:] or ["unbekannter Fehler"]
        raise UpscaleError(f"Real-ESRGAN fehlgeschlagen: {detail[0]}")
    return out


def _descreen_file(src: Path, dst: Path, strength: str) -> None:
    from PIL import Image

    with Image.open(src) as im:
        imaging.descreen(im, strength).save(dst)


async def _fetch(option: dict[str, Any], *, upscale: bool = False, cfg: dict[str, Any] | None = None) -> Path:
    """Download (cached) and make print-ready: returns a local image with bleed.

    With ``upscale`` Scryfall scans are enlarged 4x by Real-ESRGAN and rendered at 600 DPI.
    """
    if option["origin"] == "local":
        return Path(option["id"])
    raw = await download(option["full"], _cache_file(option))
    if option["origin"] == "mpcfill":
        return raw  # community scans already include the bleed edge
    if upscale:
        cfg = cfg or settings_mod.load()
        strength = cfg.get("descreen") or "off"
        ds = "" if strength == "off" else f"-ds{strength}"
        ready = raw.with_name(f"{raw.stem}-bleed{UPSCALE_DPI}-{cfg['upscale_model']}{ds}.jpg")
        if not ready.exists():
            source = raw
            if ds:  # remove the print halftone first, otherwise the AI sharpens it into lines
                source = raw.with_name(f"{raw.stem}{ds}.png")
                if not source.exists():
                    await asyncio.to_thread(_descreen_file, raw, source, strength)
            big = await _upscale(source, cfg)  # UpscaleError -> caller falls back to 300 DPI
            await asyncio.to_thread(imaging.add_bleed, big, ready, UPSCALE_DPI)
        return ready
    ready = raw.with_name(raw.stem + "-bleed.jpg")
    if not ready.exists():
        await asyncio.to_thread(imaging.add_bleed, raw, ready)
    return ready


async def _cardback(cfg: dict[str, Any], mpc: MpcFill | None) -> tuple[Path, str]:
    own = cfg.get("cardback_path")
    if own and Path(own).is_file():
        return Path(own).resolve(), "eigener Kartenrücken"
    if mpc:
        try:
            ids = await mpc.cardbacks()
            if ids:
                opt = mpc.option(ids[0], {}, "cardback")
                return await _fetch(opt), "Standard-Kartenrücken des MPC-Autofill-Servers"
        except Exception:
            pass
    path = image_cache() / "cardback-plain.jpg"
    if not path.exists():
        await asyncio.to_thread(imaging.plain_cardback, path)
    return path, "schlichter Kartenrücken (eigenen unter Einstellungen → Kartenrücken setzen)"


def _card_xml(path: Path, query: str, slots: list[int]) -> str:
    return (
        "    <card>\n"
        f"      <id>{escape(str(path))}</id>\n"
        f"      <sourceType>{LOCAL_FILE}</sourceType>\n"
        f"      <slots>{','.join(map(str, sorted(slots)))}</slots>\n"
        f"      <name>{escape(path.name)}</name>\n"
        f"      <query>{escape(query)}</query>\n"
        "    </card>\n"
    )


def order_xml(quantity: int, stock: str, foil: bool, fronts: dict[Path, tuple[str, list[int]]],
              backs: dict[Path, tuple[str, list[int]]], cardback: Path) -> str:  # fmt: skip
    xml = ['<?xml version="1.0" encoding="UTF-8"?>\n<order>\n  <details>\n',
           f"    <quantity>{quantity}</quantity>\n    <stock>{escape(stock)}</stock>\n",
           f"    <foil>{'true' if foil else 'false'}</foil>\n  </details>\n  <fronts>\n"]  # fmt: skip
    xml += [_card_xml(p, q, s) for p, (q, s) in fronts.items()]
    xml.append("  </fronts>\n")
    if backs:
        xml.append("  <backs>\n")
        xml += [_card_xml(p, q, s) for p, (q, s) in backs.items()]
        xml.append("  </backs>\n")
    xml.append(f"  <cardback>{escape(str(cardback))}</cardback>\n</order>\n")
    return "".join(xml)


async def prepare(
    deck: dict[str, Any],
    *,
    source: str = "auto",
    stock: str | None = None,
    foil: bool | None = None,
    upscale: bool | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Download + process all images and write proxies/<slug>/<slug>.xml (local files only).

    ``upscale`` (opt-in, default from settings = off): AI-upscale Scryfall scans to 600 DPI.
    """
    cfg = settings_mod.load()
    upscale = bool(cfg.get("upscale") if upscale is None else upscale)
    if upscale:
        exe = find_upscaler(cfg)
        if exe is None:
            raise ValueError(
                "KI-Hochskalierung ist aktiviert, aber Real-ESRGAN wurde nicht gefunden. Lade "
                "realesrgan-ncnn-vulkan-20220424-windows.zip (bzw. -ubuntu/-macos) von "
                "https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0 herunter, entpacke es nach "
                "tools/realesrgan/ oder trage den Pfad in den Einstellungen ein – oder deaktiviere die Option."
            )
        models = upscale_models(exe)
        if exe.suffix != ".py" and cfg["upscale_model"] not in models:
            raise ValueError(
                f"Real-ESRGAN-Modell '{cfg['upscale_model']}' fehlt in {exe.parent / 'models'} "
                f"(vorhanden: {', '.join(models) or 'keine'}). Der Ordner models/ muss neben der exe liegen."
            )
    stock = stock or cfg["stock"]
    foil = bool(cfg["foil"] if foil is None else foil)
    if stock not in STOCKS:
        raise ValueError(f"Unbekannte Kartenstärke '{stock}'. Möglich: {', '.join(STOCKS)}")
    if stock.startswith("(P10)") and foil:
        raise ValueError("Plastik-Karten (P10) gibt es nicht in Foil.")

    p = await plan(deck, source=source)
    jobs = {img["id"]: img for c in p["cards"] for side in ("front", "back") if c[side] and (img := c[side]["image"])}
    total, done = len(jobs), 0
    local: dict[str, Path] = {}
    errors: list[str] = []
    upscale_failed: dict[str, list[str]] = {}  # error message -> cards kept at 300 DPI
    sem = asyncio.Semaphore(PARALLEL_DOWNLOADS)

    async def work(key: str, option: dict[str, Any]) -> None:
        nonlocal done
        async with sem:
            try:
                try:
                    local[key] = await _fetch(option, upscale=upscale, cfg=cfg)
                except UpscaleError as exc:  # keep the card, just without AI upscaling
                    upscale_failed.setdefault(str(exc), []).append(option.get("name") or "?")
                    local[key] = await _fetch(option, upscale=False, cfg=cfg)
            except Exception as exc:
                errors.append(f"{option.get('name')}: {exc}")
            done += 1
            if progress:
                result = progress(done, total, option.get("name") or "")
                if asyncio.iscoroutine(result):
                    await result

    await asyncio.gather(*(work(k, o) for k, o in jobs.items()))
    cardback, cardback_note = await _cardback(cfg, _mpc_client(cfg, source))

    # readable, self-contained order folder: proxies/<slug>/images/<Kartenname>.jpg (hardlinks when possible)
    out = order_dir(deck["slug"])
    images_dir = out / "images"
    if images_dir.exists():
        for old in images_dir.iterdir():
            if old.is_file():
                old.unlink()
    images_dir.mkdir(parents=True, exist_ok=True)
    failed_faces = {n for names in upscale_failed.values() for n in names}
    faces_info: dict[str, dict[str, Any]] = {}
    readable: dict[str, Path] = {}  # image id -> readable file

    def publish(face: str, option: dict[str, Any]) -> Path | None:
        cached = local.get(option["id"])
        if cached is None:
            return None
        if option["id"] not in readable:
            dst = images_dir / f"{safe_filename(face)}{cached.suffix}"
            _link_or_copy(cached, dst)
            readable[option["id"]] = dst
            is_upscaled = upscale and option["origin"] == "scryfall" and face not in failed_faces
            original = _cache_file(option) if option["origin"] != "local" else cached
            faces_info[face] = {
                "file": str(dst),
                "cache": str(cached),
                "original": str(original),
                "origin": option["origin"],
                "upscaled": is_upscaled,
                "dpi": UPSCALE_DPI if is_upscaled else (option.get("dpi") or (300 if option["origin"] == "scryfall" else None)),
            }
        return readable[option["id"]]

    fronts: dict[Path, tuple[str, list[int]]] = {}
    backs: dict[Path, tuple[str, list[int]]] = {}
    manifest: list[dict[str, str | None]] = []
    slot = 0
    missing = list(p["missing"])
    for c in p["cards"]:
        front_img = c["front"]["image"]
        front = publish(c["front"]["face"], front_img) if front_img else None
        back_img = c["back"]["image"] if c["back"] else None
        back = publish(c["back"]["face"], back_img) if back_img else None
        if front is None:  # no image -> no (blank) slot at MPC
            if c["name"] not in missing:
                missing.append(c["name"])
            continue
        for _ in range(c["qty"]):
            fronts.setdefault(front, (process_query(c["front"]["face"]), []))[1].append(slot)
            if back:
                backs.setdefault(back, (process_query(c["back"]["face"]), []))[1].append(slot)
            manifest.append({"name": c["name"], "front": str(front), "back": str(back) if back else None})
            slot += 1
    cardback_file = images_dir / f"_Kartenrücken{cardback.suffix}"
    _link_or_copy(cardback, cardback_file)

    for old in out.glob("*.xml"):  # the desktop tool asks which file to use when there are several
        old.unlink()
    xml_path = out / f"{deck['slug']}.xml"
    xml_path.write_text(order_xml(slot, stock, foil, fronts, backs, cardback_file), "utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
    (out / "prepared.json").write_text(json.dumps({
        "faces": faces_info,
        "cardback": str(cardback_file),
        "upscaled": upscale,
        "upscale_model": cfg["upscale_model"] if upscale else None,
        "descreen": cfg.get("descreen") if upscale else None,
        "images_dir": str(images_dir),
    }, indent=2, ensure_ascii=False), "utf-8")  # fmt: skip

    origins = [img["origin"] for img in jobs.values()]
    warnings = list(p["warnings"])
    for message, names in upscale_failed.items():
        warnings.append(f"{message} – {len(names)} Karte(n) stattdessen mit 300 DPI: {', '.join(sorted(names)[:8])}"
                        + (" …" if len(names) > 8 else ""))  # fmt: skip
    n_failed = sum(len(v) for v in upscale_failed.values())
    if "scryfall" in origins and upscale and n_failed < origins.count("scryfall"):
        warnings.append(
            f"Scryfall-Scans wurden mit Real-ESRGAN ({cfg['upscale_model']}) auf {UPSCALE_DPI} DPI hochskaliert "
            + ("" if cfg.get("descreen", "off") == "off" else f"(vorher Druckraster entfernt: {cfg['descreen']}) ")
            + "und mit Beschnitt-Rand versehen. Stichprobenartig prüfen (🔍) – KI kann feine Details verfälschen."
        )
    elif "scryfall" in origins:
        warnings.append(
            "Scryfall-Scans (300 DPI) wurden automatisch mit Beschnitt-Rand versehen. Für mehr Schärfe: "
            "KI-Hochskalierung aktivieren, einen MPC-Autofill-Server eintragen oder im Druckstudio Bilder austauschen."
        )
    return {
        "xml": str(xml_path),
        "directory": str(out),
        "images_dir": str(images_dir),
        "quantity": slot,
        "mpc_bracket": mpc_bracket(slot),
        "stock": stock,
        "foil": foil,
        "images_mpcfill": origins.count("mpcfill"),
        "images_scryfall": origins.count("scryfall"),
        "images_custom": sum(1 for img in jobs.values() if img.get("custom")),
        "images_upscaled": origins.count("scryfall") - n_failed if upscale else 0,
        "upscaled": upscale,
        "double_faced": [c["name"] for c in p["cards"] if c["back"]],
        "missing": sorted(missing),
        "errors": errors,
        "cardback": cardback_note,
        "warnings": warnings,
    }


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink (no extra disk space) or copy if linking is impossible (other drive, FAT, ...)."""
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def load_prepared(slug: str) -> dict[str, Any] | None:
    try:
        return json.loads((order_dir(slug) / "prepared.json").read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def prepared_image(slug: str, face: str, kind: str) -> Path:
    """Path of a prepared image for the before/after view. Only files recorded in prepared.json."""
    info = (load_prepared(slug) or {}).get("faces", {}).get(face)
    if not info:
        raise FileNotFoundError(f"Keine vorbereitete Druckdatei für „{face}“ – zuerst „Druckdateien vorbereiten“.")
    path = Path(info["original"] if kind == "original" else info["file"])
    if not path.is_file():
        raise FileNotFoundError(f"Datei fehlt: {path}")
    return path


def export_pdf(slug: str, *, paper: str = "A4", include_backs: bool = True, cut_marks: bool = True) -> dict[str, Any]:
    """Home-printing PDF from a prepared order (run ``prepare`` first)."""
    manifest_path = order_dir(slug) / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Druckdateien fehlen – zuerst „Druckdateien vorbereiten“.")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    images = [Path(m["front"]) for m in manifest]
    if include_backs:
        images += [Path(m["back"]) for m in manifest if m["back"]]
    dst = order_dir(slug) / f"{slug}.pdf"
    info = imaging.make_pdf(images, dst, paper=paper, cut_marks=cut_marks)
    return {"pdf": str(dst), "paper": paper, **info}


# --- the desktop tool ---------------------------------------------------------------------------


def find_autofill(cfg: dict[str, Any] | None = None) -> Path | None:
    cfg = cfg or settings_mod.load()
    if cfg.get("autofill_path"):
        p = Path(cfg["autofill_path"]).expanduser()
        return p if p.is_file() else None
    for pattern in ("autofill*.exe", "autofill*", "autofill.py"):
        for candidate in sorted((PROJECT_ROOT / "tools").glob(pattern)):
            if candidate.is_file() and candidate.name != "README.md":
                return candidate
    return None


def autofill_command(folder: Path, *, mode: str = "mpc", cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or settings_mod.load()
    exe = find_autofill(cfg)
    if exe is None:
        raise FileNotFoundError(
            "MPC Autofill nicht gefunden. Lege autofill-windows.exe in den Ordner tools/ oder setze den Pfad "
            "unter Einstellungen. Download: https://github.com/chilli-axe/mpc-autofill/releases"
        )
    if exe.suffix.lower() == ".exe" and os.name != "nt":
        raise RuntimeError(f"{exe.name} läuft nur unter Windows – nutze die Autofill-Version für dein Betriebssystem.")
    cmd = [sys.executable, str(exe)] if exe.suffix == ".py" else [str(exe)]
    # Our images are already processed and local, so the tool skips downloading and post-processing.
    cmd += ["--directory", str(folder), "--browser", cfg["browser"], "--site", cfg["site"],
            "--auto-save", "--no-image-post-processing", "--allowsleep" if mode == "pdf" else "--disallow-sleep"]  # fmt: skip
    if mode == "pdf":
        cmd.append("--exportpdf")
    return cmd


def _check_order(folder: Path) -> None:
    if not any(folder.glob("*.xml")):
        raise FileNotFoundError("Keine Druckdateien – zuerst „Druckdateien vorbereiten“ (create_proxy_order).")


def launch_autofill(folder: Path, *, mode: str = "mpc") -> dict[str, Any]:
    """Start the desktop tool in its own console window (used by the MCP tool; the GUI streams it)."""
    _check_order(folder)
    cmd = autofill_command(folder, mode=mode)
    kwargs: dict[str, Any] = {"cwd": str(folder)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    else:
        log = (folder / "autofill.log").open("ab")
        kwargs.update(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    proc = subprocess.Popen(cmd, **kwargs)
    return {"started": True, "pid": proc.pid, "mode": mode, "command": cmd}
