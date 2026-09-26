"""Import existing decks from deckbuilding sites.

- Archidekt: public read API, no key (https://archidekt.com/api/decks/<id>/).
- Moxfield: no public API. Their internal API is Cloudflare-protected and requires an
  approved User-Agent, so we only try it best-effort and otherwise ask for the text export.
"""

from __future__ import annotations

import re
from typing import Any

from .deck import DeckEntry, ParsedList
from .http import HttpError, get_json

_ARCHIDEKT_RE = re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)")
_MOXFIELD_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)")


async def import_archidekt(url_or_id: str) -> dict[str, Any]:
    m = _ARCHIDEKT_RE.search(url_or_id)
    deck_id = m.group(1) if m else url_or_id.strip()
    if not deck_id.isdigit():
        raise ValueError("Expected an Archidekt deck URL or numeric id")
    data = await get_json(f"https://archidekt.com/api/decks/{deck_id}/", ttl=600)

    parsed = ParsedList()
    for entry in data.get("cards", []):
        cats = set(entry.get("categories") or [])
        name = (((entry.get("card") or {}).get("oracleCard") or {}).get("name")) or ""
        if not name or cats & {"Maybeboard", "Sideboard"}:
            continue
        if "Commander" in cats:
            parsed.commanders.append(name)
        else:
            parsed.entries.append(DeckEntry(name, int(entry.get("quantity") or 1)))
    return {
        "source": f"https://archidekt.com/decks/{deck_id}",
        "name": data.get("name"),
        "commanders": parsed.commanders,
        "cards": [f"{e.qty} {e.name}" for e in parsed.entries],
    }


async def import_moxfield(url_or_id: str) -> dict[str, Any]:
    m = _MOXFIELD_RE.search(url_or_id)
    deck_id = m.group(1) if m else url_or_id.strip()
    try:
        data = await get_json(f"https://api2.moxfield.com/v3/decks/all/{deck_id}", ttl=600)
    except HttpError as exc:
        raise RuntimeError(
            "Moxfield has no public API and blocked the request "
            f"(HTTP {exc.status}). In Moxfield use 'Export' -> 'Copy for MTGA/Plain text' and paste the list instead."
        ) from exc

    boards = data.get("boards") or {}

    def names(board: str) -> list[tuple[str, int]]:
        cards = (boards.get(board) or {}).get("cards") or {}
        return [((v.get("card") or {}).get("name", ""), int(v.get("quantity") or 1)) for v in cards.values()]

    return {
        "source": f"https://moxfield.com/decks/{deck_id}",
        "name": data.get("name"),
        "commanders": [n for n, _ in names("commanders")],
        "cards": [f"{q} {n}" for n, q in names("mainboard")],
    }


async def import_url(url: str) -> dict[str, Any]:
    if "archidekt.com" in url:
        return await import_archidekt(url)
    if "moxfield.com" in url:
        return await import_moxfield(url)
    raise ValueError("Supported: archidekt.com and moxfield.com deck URLs. Otherwise paste the decklist as text.")
