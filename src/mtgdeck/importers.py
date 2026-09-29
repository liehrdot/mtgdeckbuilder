"""Import existing decks from deckbuilding sites or a pasted list.

Every importer returns the same shape::

    {"source", "site", "name", "commanders": [...], "cards": ["1 Sol Ring", ...],
     "categories": {card: deck category}, "commander_hint": str | None}

- Archidekt: public read API (https://archidekt.com/api/decks/<id>/), incl. its categories
  (categories marked "not in deck", maybeboard and sideboard are skipped).
- Moxfield: no public API; the internal one is Cloudflare-protected, so it is best effort (v3,
  then v2) and otherwise the error asks for the text export.
- MTGGoldfish (``/deck/download/<id>``), TappedOut (``?fmt=txt``) and Deckstats
  (``?export_txt=1``) offer plain-text exports.
- EDHREC average decks (``/average-decks/<commander>`` or ``/commanders/<commander>``).
- Pasted text: plain lists, Moxfield/MTGA/Archidekt exports, ``*CMDR*`` or a Commander section.

When a source does not mark the commander, ``commanders`` stays empty; ``commander_hint`` (e.g.
the EDHREC slug or a small last block of a goldfish list) helps to preselect one.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .deck import parse_decklist
from .http import HttpError, get_json, get_text

_ARCHIDEKT_RE = re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)")
_MOXFIELD_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)")
_GOLDFISH_RE = re.compile(r"mtggoldfish\.com/deck/(?:download/)?(\d+)")
_TAPPEDOUT_RE = re.compile(r"tappedout\.net/mtg-decks/([A-Za-z0-9_-]+)")
_DECKSTATS_RE = re.compile(r"deckstats\.net/decks/(\d+)/(\d+)([A-Za-z0-9_-]*)")
_EDHREC_RE = re.compile(r"edhrec\.com/(?:average-decks|commanders)/([a-z0-9-]+)(?:/(budget|expensive))?")

SITES = ["Archidekt", "Moxfield", "MTGGoldfish", "TappedOut", "Deckstats", "EDHREC (Durchschnittsdeck)"]

# words in a site's own category names -> our fixed deck categories (references/deck-template.md)
_CATEGORY_WORDS = [
    ("board wipe", "Board Wipe"), ("wipe", "Board Wipe"), ("sweeper", "Board Wipe"), ("wrath", "Board Wipe"),
    ("ramp", "Ramp"), ("mana rock", "Ramp"), ("draw", "Draw"), ("card advantage", "Draw"),
    ("removal", "Removal"), ("interaction", "Removal"), ("counter", "Removal"), ("protection", "Protection"),
    ("win", "Win Condition"), ("finisher", "Win Condition"), ("land", "Land"), ("utility", "Utility"),
]  # fmt: skip


def map_category(name: str) -> str:
    low = name.lower()
    return next((cat for word, cat in _CATEGORY_WORDS if word in low), "")


def _result(site: str, source: str, name: Any, commanders: list[str], cards: list[tuple[str, int]],
            categories: dict[str, str] | None = None, hint: str | None = None) -> dict[str, Any]:  # fmt: skip
    merged: dict[str, int] = {}
    for n, q in cards:
        if n and n not in commanders:
            merged[n] = merged.get(n, 0) + q
    return {"source": source, "site": site, "name": str(name or "").strip() or None, "commanders": commanders,
            "cards": [f"{q} {n}" for n, q in merged.items()], "categories": categories or {}, "commander_hint": hint}  # fmt: skip


def _from_text(text: str, site: str, source: str, name: str | None = None) -> dict[str, Any]:
    """Parse a text export; deckstats' ``# !Commander`` comments count as commander markers."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if re.search(r"#\s*!?commander\b", line, re.I):
            line = re.sub(r"\s*#.*$", "", line) + " *CMDR*"
        elif "#" in line and not line.startswith("#"):
            line = re.sub(r"\s+#.*$", "", line)
        lines.append(line)
    parsed = parse_decklist(lines)
    hint = None
    if not parsed.commanders:  # goldfish & co: the commander often sits in a small last block
        blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
        if len(blocks) >= 2 and 1 <= len(blocks[-1].splitlines()) <= 2:
            last = parse_decklist(blocks[-1])
            hint = last.entries[0].name if last.entries else None
    return _result(site, source, name, parsed.commanders, [(e.name, e.qty) for e in parsed.entries], hint=hint)


def import_text(text: str, name: str | None = None) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("Die Liste ist leer.")
    return _from_text(text, "Liste", "", name)


async def import_archidekt(url_or_id: str) -> dict[str, Any]:
    m = _ARCHIDEKT_RE.search(url_or_id)
    deck_id = m.group(1) if m else url_or_id.strip()
    if not deck_id.isdigit():
        raise ValueError("Erwartet eine Archidekt-Deck-URL oder -ID")
    data = await get_json(f"https://archidekt.com/api/decks/{deck_id}/", ttl=600)
    excluded = {"Maybeboard", "Sideboard"} | {
        c.get("name") for c in data.get("categories") or [] if isinstance(c, dict) and c.get("includedInDeck") is False
    }
    commanders: list[str] = []
    cards: list[tuple[str, int]] = []
    categories: dict[str, str] = {}
    for entry in data.get("cards", []):
        cats = [c for c in entry.get("categories") or [] if isinstance(c, str)]
        name = (((entry.get("card") or {}).get("oracleCard") or {}).get("name")) or ""
        if not name or (cats and all(c in excluded for c in cats)):
            continue
        if "Commander" in cats:
            commanders.append(name)
            continue
        cards.append((name, int(entry.get("quantity") or 1)))
        cat = next((map_category(c) for c in cats if map_category(c)), "")
        if cat:
            categories[name] = cat
    return _result("Archidekt", f"https://archidekt.com/decks/{deck_id}", data.get("name"), commanders, cards, categories)


def _moxfield_board(board: Any) -> list[tuple[str, int]]:
    cards = (board or {}).get("cards", board) if isinstance(board, dict) else {}
    out = []
    for v in (cards or {}).values() if isinstance(cards, dict) else []:
        if isinstance(v, dict) and (v.get("card") or {}).get("name"):
            out.append((v["card"]["name"], int(v.get("quantity") or 1)))
    return out


async def import_moxfield(url_or_id: str) -> dict[str, Any]:
    m = _MOXFIELD_RE.search(url_or_id)
    deck_id = m.group(1) if m else url_or_id.strip()
    data: dict[str, Any] | None = None
    status = 0
    for api in (f"https://api2.moxfield.com/v3/decks/all/{deck_id}", f"https://api.moxfield.com/v2/decks/all/{deck_id}"):
        try:
            data = await get_json(api, ttl=600)
            break
        except HttpError as exc:
            status = exc.status
            if exc.status == 404:
                raise ValueError("Moxfield kennt dieses Deck nicht – ist es öffentlich?") from exc
    if data is None:
        raise RuntimeError(
            f"Moxfield hat die Anfrage blockiert (HTTP {status}); eine offene Schnittstelle gibt es dort nicht. "
            "Öffne das Deck in Moxfield, wähle „Export“ → „Copy for MTGA“ oder „Copy Plain Text“ und füge die Liste "
            "unter „Liste einfügen“ ein."
        )
    boards = data.get("boards") or data  # v3: boards.<board>.cards, v2: <board> at the top level
    commanders = [n for n, _ in _moxfield_board(boards.get("commanders"))]
    return _result("Moxfield", f"https://moxfield.com/decks/{deck_id}", data.get("name"), commanders,
                   _moxfield_board(boards.get("mainboard")))  # fmt: skip


async def import_goldfish(url: str) -> dict[str, Any]:
    deck_id = _GOLDFISH_RE.search(url).group(1)  # type: ignore[union-attr]
    text = await get_text(f"https://www.mtggoldfish.com/deck/download/{deck_id}", ttl=600)
    return _from_text(text, "MTGGoldfish", f"https://www.mtggoldfish.com/deck/{deck_id}")


async def import_tappedout(url: str) -> dict[str, Any]:
    slug = _TAPPEDOUT_RE.search(url).group(1)  # type: ignore[union-attr]
    text = await get_text(f"https://tappedout.net/mtg-decks/{slug}/", {"fmt": "txt"}, ttl=600)
    name = slug.replace("-", " ").strip().title()
    return _from_text(text, "TappedOut", f"https://tappedout.net/mtg-decks/{slug}/", name)


async def import_deckstats(url: str) -> dict[str, Any]:
    m = _DECKSTATS_RE.search(url)
    user, deck_id, rest = m.group(1), m.group(2), m.group(3)  # type: ignore[union-attr]
    page = f"https://deckstats.net/decks/{user}/{deck_id}{rest}/"
    text = await get_text(page, {"export_txt": 1}, ttl=600)
    name = rest.strip("-").replace("-", " ").title() or None
    return _from_text(text, "Deckstats", page, name)


async def import_edhrec(url: str) -> dict[str, Any]:
    m = _EDHREC_RE.search(url)
    slug, budget = m.group(1), m.group(2)  # type: ignore[union-attr]
    path = slug + (f"/{budget}" if budget else "")
    try:
        page = await get_json(f"https://json.edhrec.com/pages/average-decks/{path}.json")
    except HttpError as exc:
        if exc.status in (403, 404):
            raise ValueError(f"EDHREC hat kein Durchschnittsdeck für „{slug}“.") from exc
        raise
    lines = page.get("deck") or []
    if not isinstance(lines, list) or not lines:
        raise ValueError("EDHREC-Seite ohne Deckliste.")
    res = _from_text("\n".join(str(x) for x in lines), "EDHREC", f"https://edhrec.com/average-decks/{path}")
    res["name"] = f"EDHREC-Durchschnitt: {slug.replace('-', ' ').title()}" + (f" ({budget})" if budget else "")
    res["commander_hint"] = slug  # matched against the slugified names of the legendary cards
    return res


async def import_url(url: str) -> dict[str, Any]:
    url = url.strip()
    host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
    if "archidekt.com" in host:
        return await import_archidekt(url)
    if "moxfield.com" in host:
        return await import_moxfield(url)
    if "mtggoldfish.com" in host and _GOLDFISH_RE.search(url):
        return await import_goldfish(url)
    if "tappedout.net" in host and _TAPPEDOUT_RE.search(url):
        return await import_tappedout(url)
    if "deckstats.net" in host and _DECKSTATS_RE.search(url):
        return await import_deckstats(url)
    if "edhrec.com" in host and _EDHREC_RE.search(url):
        return await import_edhrec(url)
    raise ValueError(f"Diese Seite kenne ich nicht. Unterstützt: {', '.join(SITES)}. "
                     "Sonst die Liste auf der Seite exportieren und unter „Liste einfügen“ einfügen.")  # fmt: skip

