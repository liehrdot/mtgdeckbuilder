"""The user's card collection: what they own, how many, which printing (artwork), real or proxy.

Stored as JSON (``MTG_COLLECTION_FILE``, default ``<repo>/collection.json``, gitignored). One entry
per printing: name (English Oracle name), qty, proxy, foil, lang, set/set_name/collector_number/
scryfall_id/image (the artwork), price at import, note, added. Entries that only differ in quantity
are merged.

Imports understand the CSV exports of ManaBox, Moxfield (incl. its Proxy column) and Archidekt as
well as plain lists ("2 Sol Ring (C21) 263 *F*"). ``deck_ownership`` compares a deck with the
collection (real / proxy / missing, shopping list, cards shared by several decks).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import scryfall, storage
from .cards import resolve

COLLECTION_FILE = Path(os.environ.get("MTG_COLLECTION_FILE", storage.PROJECT_ROOT / "collection.json"))

BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"} | {
    f"Snow-Covered {b}" for b in ("Plains", "Island", "Swamp", "Mountain", "Forest")
}
PRINTING_KEYS = ("scryfall_id", "set", "set_name", "collector_number", "image", "image_back", "price_eur", "price_usd")
_LANGS = {"english": "en", "englisch": "en", "german": "de", "deutsch": "de", "french": "fr", "französisch": "fr",
          "italian": "it", "italienisch": "it", "spanish": "es", "spanisch": "es", "portuguese": "pt",
          "japanese": "ja", "japanisch": "ja", "korean": "ko", "russian": "ru", "chinese simplified": "zhs",
          "simplified chinese": "zhs", "chinese traditional": "zht", "traditional chinese": "zht"}  # fmt: skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- storage ------------------------------------------------------------------------------------


def load() -> list[dict[str, Any]]:
    try:
        data = json.loads(COLLECTION_FILE.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write(entries: list[dict[str, Any]]) -> None:
    COLLECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = COLLECTION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(COLLECTION_FILE)


def _key(e: dict[str, Any]) -> tuple[Any, ...]:
    return (e["name"].lower(), e.get("scryfall_id") or "", bool(e.get("proxy")), bool(e.get("foil")), e.get("lang") or "en")


def _merge(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine entries that describe the same thing (same card, printing, proxy, foil, language)."""
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for e in entries:
        k = _key(e)
        if k in out:
            out[k]["qty"] += int(e.get("qty", 1))
        else:
            out[k] = e
    return [e for e in out.values() if e["qty"] > 0]


def _entry(name: str, *, qty: int = 1, proxy: bool = False, foil: bool = False, lang: str = "en",
           note: str = "", **printing: Any) -> dict[str, Any]:  # fmt: skip
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "qty": max(1, int(qty)),
        "proxy": bool(proxy),
        "foil": bool(foil),
        "lang": lang or "en",
        **{k: printing.get(k) for k in PRINTING_KEYS},
        "note": note,
        "added": _now(),
    }


# --- parsing imports ------------------------------------------------------------------------------

_COLS = {
    "name": ("name", "card name", "card", "kartenname", "karte"),
    "qty": ("quantity", "count", "qty", "anzahl", "amount", "menge"),
    "set": ("set code", "edition code", "edition", "set", "setcode"),
    "collector_number": ("collector number", "collector_number", "card number", "collector #", "cn", "number", "nr"),
    "scryfall_id": ("scryfall id", "scryfall_id", "scryfallid", "scryfall uuid"),
    "foil": ("foil", "finish", "printing"),
    "proxy": ("proxy",),
    "lang": ("language", "lang", "sprache"),
}
_TRUE = {"1", "true", "yes", "ja", "y", "x", "foil", "etched", "proxy"}
_LINE_RE = re.compile(
    r"^\s*(?:(?P<qty>\d+)\s*x?\s+)?(?P<name>.+?)"
    r"(?:\s+[(\[](?!proxy[)\]])(?P<set>[A-Za-z0-9]{2,6})[)\]](?:\s+#?(?P<cn>[\w★†\-]+))?)?"
    r"(?P<flags>(?:\s+(?:\*F\*|\*E\*|\*P\*|\[proxy\]|\(proxy\)))*)\s*$",
    re.IGNORECASE,
)


def _lang(value: str) -> str:
    v = (value or "").strip().lower()
    return _LANGS.get(v, v if len(v) in (2, 3) else "en")


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in _TRUE


def parse_import(text: str) -> list[dict[str, Any]]:
    """CSV (ManaBox, Moxfield, Archidekt, …) or a plain card list -> raw items."""
    text = text.lstrip("﻿").strip()
    if not text:
        return []
    first = text.splitlines()[0]
    delim = max((",", ";", "\t"), key=first.count)
    header = [h.strip().lower() for h in next(csv.reader([first], delimiter=delim))]
    if any(h in _COLS["name"] for h in header) and first.count(delim) >= 1:
        return _parse_csv(text, delim)
    return _parse_lines(text)


def _parse_csv(text: str, delim: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    fields = {f.strip().lower(): f for f in reader.fieldnames or []}
    col = {key: next((fields[a] for a in aliases if a in fields), None) for key, aliases in _COLS.items()}
    items = []
    for row in reader:
        get = lambda k: (row.get(col[k]) or "").strip() if col[k] else ""  # noqa: E731
        name = get("name")
        if not name and not get("scryfall_id"):
            continue
        qty = get("qty")
        items.append({
            "name": name,
            "qty": int(qty) if qty.isdigit() else 1,
            "set": get("set").lower() or None,
            "collector_number": get("collector_number") or None,
            "scryfall_id": get("scryfall_id") or None,
            "foil": _truthy(get("foil")),
            "proxy": _truthy(get("proxy")),
            "lang": _lang(get("lang")) if get("lang") else None,
        })  # fmt: skip
    return items


def _parse_lines(text: str) -> list[dict[str, Any]]:
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")) or line.lower().rstrip(":") in {"deck", "commander", "sideboard", "maybeboard"}:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        flags = (m.group("flags") or "").lower()
        items.append({
            "name": m.group("name").strip(),
            "qty": int(m.group("qty") or 1),
            "set": (m.group("set") or "").lower() or None,
            "collector_number": m.group("cn"),
            "scryfall_id": None,
            "foil": "*f*" in flags or "*e*" in flags,
            "proxy": "*p*" in flags or "proxy" in flags,
            "lang": None,
        })  # fmt: skip
    return items


# --- resolving names + printings ------------------------------------------------------------------


async def _resolve_items(items: list[dict[str, Any]], *, proxy_default: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """Raw items -> collection entries (English names, printing/artwork where known)."""
    items = [{**it, "qty": int(it.get("qty") or 1)} for it in items]
    entries: list[dict[str, Any]] = []
    need_name: list[dict[str, Any]] = []
    for it in [it for it in items if it.get("printing") and it["printing"].get("name")]:
        p = it["printing"]  # chosen in the artwork picker: all facts already known
        entries.append(_entry(p["name"], qty=it["qty"], proxy=it.get("proxy") or proxy_default, foil=it.get("foil", False),
                              lang=it.get("lang") or p.get("lang") or "en", note=it.get("note") or "",
                              **{k: p.get(k) for k in PRINTING_KEYS}))  # fmt: skip
    items = [it for it in items if not (it.get("printing") and it["printing"].get("name"))]
    with_print = [it for it in items if it.get("scryfall_id") or (it.get("set") and it.get("collector_number"))]
    idents = [{"id": it["scryfall_id"]} if it.get("scryfall_id") else {"set": it["set"], "collector_number": str(it["collector_number"])}
              for it in with_print]  # fmt: skip
    found: list[dict[str, Any] | None] = []
    if idents:
        try:
            found, _ = await scryfall.by_identifiers(idents)
        except Exception:  # offline: fall back to names
            found = [None] * len(idents)
    for it, card in zip(with_print, found):
        if card is None:
            need_name.append(it)
            continue
        p = scryfall.printing(card, foil=it.get("foil", False))
        lang = it.get("lang") or p.pop("lang", None) or "en"
        p.pop("lang", None)
        entries.append(_entry(p.pop("name"), qty=it["qty"], proxy=it.get("proxy") or proxy_default,
                              foil=it.get("foil", False), lang=lang, **p))  # fmt: skip
    printed = {id(it) for it in with_print}
    need_name += [it for it in items if id(it) not in printed]
    not_found: list[str] = []
    named = [it for it in need_name if it.get("name")]
    if named:
        data, renames, missing = await resolve([it["name"] for it in named])
        missing_set = set(missing)
        for it in named:
            if it["name"].strip() in missing_set:
                not_found.append(it["name"])
                continue
            name = renames.get(it["name"].strip(), it["name"].strip())
            c = data.get(name, {})
            entries.append(_entry(name, qty=it["qty"], proxy=it.get("proxy") or proxy_default, foil=it.get("foil", False),
                                  lang=it.get("lang") or "en", note=it.get("note") or "",
                                  image=it.get("image") or c.get("image"), image_back=c.get("image_back"),
                                  price_eur=c.get("price_eur"), price_usd=c.get("price_usd")))  # fmt: skip
    not_found += [it.get("scryfall_id") or "?" for it in need_name if not it.get("name")]
    return entries, not_found


async def add(items: list[dict[str, Any]], *, proxy_default: bool = False, replace: bool = False) -> dict[str, Any]:
    """Add raw items (``name``/``qty``/``proxy``/``foil``/``lang``/``set``/``collector_number``/``scryfall_id``)."""
    new, not_found = await _resolve_items(items, proxy_default=proxy_default)
    before = [] if replace else load()
    merged = _merge(before + new)
    _write(merged)
    return {"added": sum(e["qty"] for e in new), "entries": len(merged), "not_found": not_found}


_SCRYFALL_ID_RE = re.compile(r"/front/[0-9a-f]/[0-9a-f]/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


async def add_printed(plan: dict[str, Any]) -> dict[str, Any]:
    """After printing proxies: add the printed cards as proxy entries with the printed artwork."""
    items = []
    for c in plan.get("cards", []):
        img = (c.get("front") or {}).get("image") or {}
        m = _SCRYFALL_ID_RE.search(img.get("full") or img.get("thumb") or "")
        item: dict[str, Any] = {"name": c["name"], "qty": c["qty"], "proxy": True}
        if m:
            item["scryfall_id"] = m.group(1)
        elif img:
            item["image"] = img.get("thumb")
            item["note"] = f"{'MPC Autofill' if img.get('origin') == 'mpcfill' else 'Eigenes Bild'}: {img.get('label') or ''}".strip(": ")
        items.append(item)
    return await add(items)


async def import_text(text: str, *, proxy: bool = False, replace: bool = False) -> dict[str, Any]:
    items = parse_import(text)
    if not items:
        raise ValueError("Keine Karten erkannt – erwartet wird eine CSV mit Spalte „Name“ oder eine Liste wie „2 Sol Ring“.")
    return {**await add(items, proxy_default=proxy, replace=replace), "lines": len(items)}


def update(entry_id: str, *, qty: int | None = None, proxy: bool | None = None, foil: bool | None = None,
           lang: str | None = None, note: str | None = None, printing: dict[str, Any] | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Change one entry; ``qty <= 0`` deletes it. Returns the (possibly merged) entry or None."""
    entries = load()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        raise KeyError(entry_id)
    if qty is not None:
        entry["qty"] = int(qty)
    if proxy is not None:
        entry["proxy"] = bool(proxy)
    if foil is not None:
        entry["foil"] = bool(foil)
    if lang is not None:
        entry["lang"] = lang or "en"
    if note is not None:
        entry["note"] = note
    if printing is not None:
        entry.update({k: printing.get(k) for k in PRINTING_KEYS})
    merged = _merge(entries)
    _write(merged)
    return next((e for e in merged if _key(e) == _key(entry)), None)


def delete(entry_id: str | None = None) -> int:
    """Delete one entry, or everything when ``entry_id`` is None. Returns the number removed."""
    entries = load()
    keep = [e for e in entries if entry_id is not None and e["id"] != entry_id]
    _write(keep)
    return len(entries) - len(keep)


def to_csv(entries: list[dict[str, Any]] | None = None) -> str:
    """Moxfield-compatible CSV (also readable by ManaBox/Archidekt imports)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Count", "Name", "Edition", "Collector Number", "Foil", "Language", "Proxy", "Scryfall ID"])
    for e in sorted(entries if entries is not None else load(), key=lambda e: e["name"].lower()):
        w.writerow([e["qty"], e["name"], e.get("set") or "", e.get("collector_number") or "", "foil" if e.get("foil") else "",
                    e.get("lang") or "en", "True" if e.get("proxy") else "", e.get("scryfall_id") or ""])  # fmt: skip
    return buf.getvalue()


# --- queries -----------------------------------------------------------------------------------


def owned_counts(entries: list[dict[str, Any]] | None = None) -> dict[str, dict[str, int]]:
    """Card name -> {"real": n, "proxy": m} (also indexed by the front face of multi-face cards)."""
    out: dict[str, dict[str, int]] = {}
    for e in entries if entries is not None else load():
        kind = "proxy" if e.get("proxy") else "real"
        for key in {e["name"], e["name"].split(" // ")[0]}:
            slot = out.setdefault(key, {"real": 0, "proxy": 0})
            slot[kind] += int(e["qty"])
    return out


def summary(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    entries = entries if entries is not None else load()
    real = sum(e["qty"] for e in entries if not e.get("proxy"))
    proxy = sum(e["qty"] for e in entries if e.get("proxy"))

    def value(cur: str) -> float:
        return round(sum(float(e.get(f"price_{cur}") or 0) * e["qty"] for e in entries if not e.get("proxy")), 2)

    return {"entries": len(entries), "cards": real + proxy, "real": real, "proxy": proxy,
            "unique": len({e["name"] for e in entries}), "value_eur": value("eur"), "value_usd": value("usd")}  # fmt: skip


def decks_by_card() -> dict[str, list[dict[str, Any]]]:
    """Card name -> decks that use it (slug, name, qty)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for meta in storage.list_decks():
        try:
            deck = storage.load(meta["slug"])
        except FileNotFoundError:
            continue
        need = Counter({c: 1 for c in deck.get("commanders", [])})
        for c in deck.get("cards", []):
            need[c["name"]] += int(c.get("qty", 1))
        for name, qty in need.items():
            out.setdefault(name, []).append({"slug": deck["slug"], "name": deck.get("name", deck["slug"]), "qty": qty})
    return out


async def deck_ownership(deck: dict[str, Any], *, card_data: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compare a deck with the collection: per card real/proxy/missing, totals, shopping list."""
    entries = load()
    owned = owned_counts(entries)
    need = Counter({c: 1 for c in deck.get("commanders", [])})
    for c in deck.get("cards", []):
        need[c["name"]] += int(c.get("qty", 1))
    usage = decks_by_card() if entries else {}
    if card_data is None:
        try:
            card_data, _, _ = await resolve(list(need))
        except Exception:
            card_data = {}
    cur = "usd" if deck.get("currency") == "usd" else "eur"
    cards: dict[str, dict[str, Any]] = {}
    totals = Counter()
    shopping = []
    for name, n in need.items():
        o = owned.get(name, {"real": 0, "proxy": 0})
        basic = name in BASIC_LANDS
        real_used = min(o["real"], n)
        proxy_used = min(o["proxy"], n - real_used)
        missing = 0 if basic else n - real_used - proxy_used
        others = [d for d in usage.get(name, []) if d["slug"] != deck.get("slug")]
        total_need = n + sum(d["qty"] for d in others)
        cards[name] = {
            "need": n, "real": o["real"], "proxy": o["proxy"], "missing": missing, "basic": basic,
            "other_decks": [d["name"] for d in others],
            "shared_shortage": bool(others) and not basic and o["real"] + o["proxy"] < total_need,
        }  # fmt: skip
        totals["need"] += n
        totals["real"] += n if basic else real_used
        totals["proxy"] += 0 if basic else proxy_used
        totals["missing"] += missing
        if missing:
            price = float((card_data.get(name) or {}).get(f"price_{cur}") or 0)
            shopping.append({"name": name, "qty": missing, "price": round(price * missing, 2) if price else None})
    shopping.sort(key=lambda s: s["name"])
    return {
        "collection_size": len(entries),
        "currency": cur,
        "cards": cards,
        "need": totals["need"],
        "have_real": totals["real"],
        "have_proxy": totals["proxy"],
        "missing": totals["missing"],
        "missing_price": round(sum(s["price"] or 0 for s in shopping), 2),
        "shopping": shopping,
        "shopping_text": "\n".join(f"{s['qty']} {s['name']}" for s in shopping),
        "shared_shortages": sorted(n for n, c in cards.items() if c["shared_shortage"]),
    }


def missing_counts(deck: dict[str, Any]) -> dict[str, int]:
    """Card name -> copies not covered by the collection (real or proxy); basics count as owned."""
    owned = owned_counts()
    need = Counter({c: 1 for c in deck.get("commanders", [])})
    for c in deck.get("cards", []):
        need[c["name"]] += int(c.get("qty", 1))
    out = {}
    for name, n in need.items():
        o = owned.get(name, {"real": 0, "proxy": 0})
        miss = 0 if name in BASIC_LANDS else max(0, n - o["real"] - o["proxy"])
        out[name] = miss
    return out


async def search_owned(color_identity: str | None = None, *, text: str = "", type_contains: str = "",
                       limit: int = 300) -> list[dict[str, Any]]:  # fmt: skip
    """Owned cards (real + proxy) that fit inside ``color_identity`` – for building from the collection."""
    counts = owned_counts()
    names = sorted({e["name"] for e in load()})
    if not names:
        return []
    data, _, _ = await resolve(names)
    allowed = set((color_identity or "WUBRG").upper()) & set("WUBRG")
    out = []
    for name in names:
        c = data.get(name)
        if not c or not c.get("commander_legal", True):
            continue
        if color_identity is not None and not set(c.get("color_identity", [])) <= allowed:
            continue
        if text and text.lower() not in (c.get("oracle_text") or "").lower():
            continue
        if type_contains and type_contains.lower() not in (c.get("type_line") or "").lower():
            continue
        o = counts.get(name, {"real": 0, "proxy": 0})
        out.append({"name": name, "real": o["real"], "proxy": o["proxy"], "type_line": c.get("type_line"),
                    "cmc": c.get("cmc"), "roles": c.get("roles"), "edhrec_rank": c.get("edhrec_rank")})  # fmt: skip
    out.sort(key=lambda c: c.get("edhrec_rank") or 10**6)
    return out[:limit]
