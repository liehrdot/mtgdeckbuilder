"""Preconstructed Commander decks ("Precons", starter decks) from MTGJSON (https://mtgjson.com),
free and without an API key: ``DeckList.json`` lists all products, ``decks/<fileName>.json`` holds
one deck. Both are cached (the list for a day, deck files for a month).

MTGJSON v5 shapes (parsed defensively, unknown fields ignored)::

    DeckList.json  {"data": [{"code", "fileName", "name", "releaseDate", "type"}, ...]}
    decks/X.json   {"data": {"name", "code", "releaseDate", "type",
                             "commander": [{"name", "count"}, ...], "mainBoard": [...], "sideBoard": [...]}}
"""

from __future__ import annotations

import re
from typing import Any

from . import storage
from .deck import BASIC_LANDS
from .http import get_json

BASE = "https://mtgjson.com/api/v5"
LIST_TTL = 24 * 3600
DECK_TTL = 30 * 24 * 3600
_FILE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _is_commander_product(entry: dict[str, Any]) -> bool:
    return "commander" in str(entry.get("type") or "").lower()


async def search(query: str = "", *, limit: int = 60) -> list[dict[str, Any]]:
    """Commander precons, newest first, filtered by name, set code or year (all words must match)."""
    data = await get_json(f"{BASE}/DeckList.json", ttl=LIST_TTL)
    items = data.get("data") if isinstance(data, dict) else None
    words = query.lower().split()
    out = []
    for e in items if isinstance(items, list) else []:
        if not isinstance(e, dict) or not e.get("fileName") or not _is_commander_product(e):
            continue
        entry = {"file": str(e["fileName"]), "name": str(e.get("name") or e["fileName"]), "code": str(e.get("code") or ""),
                 "released": str(e.get("releaseDate") or ""), "type": str(e.get("type") or "")}  # fmt: skip
        hay = f"{entry['name']} {entry['code']} {entry['released'][:4]}".lower()
        if all(w in hay for w in words):
            out.append(entry)
    out.sort(key=lambda e: e["released"], reverse=True)
    return out[:limit]


def _cards(section: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in section if isinstance(section, list) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        try:
            n = max(1, int(c.get("count") or 1))
        except (TypeError, ValueError):
            n = 1
        counts[str(c["name"])] = counts.get(str(c["name"]), 0) + n
    return counts


async def load(file_name: str) -> dict[str, Any]:
    """One precon: ``{"file", "name", "code", "released", "commanders", "cards": [{"name", "qty"}]}``."""
    if not _FILE_RE.match(file_name):
        raise ValueError(f"Ungültiger Deck-Name: {file_name}")
    data = await get_json(f"{BASE}/decks/{file_name}.json", ttl=DECK_TTL)
    d = data.get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict):
        raise ValueError(f"Unerwartetes Format der MTGJSON-Datei {file_name}")
    commanders = list(_cards(d.get("commander")))
    main = _cards(d.get("mainBoard"))
    if not commanders:  # very old files: the legendary face-commander sits in the main board
        commanders = list(_cards(d.get("displayCommander")))[:2]
    for c in commanders:
        main.pop(c, None)
    if not commanders or not main:
        raise ValueError(f"{d.get('name') or file_name}: keine Commander- oder Kartenliste gefunden")
    return {
        "file": file_name, "name": str(d.get("name") or file_name), "code": str(d.get("code") or ""),
        "released": str(d.get("releaseDate") or ""), "commanders": commanders,
        "cards": [{"name": n, "qty": q} for n, q in main.items()],
        "card_count": len(commanders) + sum(main.values()),
    }  # fmt: skip


async def import_precon(file_name: str, *, bracket: int = 2, currency: str = "eur", name: str | None = None) -> dict[str, Any]:
    """Save a precon as a new deck (validated, categories guessed) and return its storage paths."""
    from .deckedit import guess_category  # deckedit imports validate, keep this module light
    from .validate import validate_deck

    p = await load(file_name)
    lines = [f"{c['qty']} {c['name']}" for c in p["cards"]]
    result = await validate_deck(p["commanders"], lines, bracket, currency=currency)
    card_data = result.pop("_card_data")
    year = f", {p['released'][:4]}" if p["released"] else ""
    deck_name = name or p["name"]
    deck = {
        "name": deck_name,
        "slug": storage.unique_slug(deck_name),
        "commanders": result["commanders"],
        "bracket": bracket,
        "description": f"Vorgefertigtes Commander-Deck „{p['name']}“ ({p['code']}{year}).",
        "strategy": "",
        "budget": None,
        "proxy": False,
        "power_profile": None,
        "currency": currency,
        "notes": "",
        "precon": {"file": p["file"], "name": p["name"], "code": p["code"], "released": p["released"]},
        "change_note": f"Importiert: Precon „{p['name']}“",
        "cards": [{**c, "category": "Land" if c["name"] in BASIC_LANDS else guess_category(card_data.get(c["name"], {}))}
                  for c in result.pop("cards")],  # fmt: skip
        "validation": result,
    }
    return storage.save(deck)
