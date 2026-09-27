"""Scryfall API client (https://scryfall.com/docs/api). No key needed."""

from __future__ import annotations

from typing import Any

from .http import HttpError, get_json, post_json

BASE = "https://api.scryfall.com"
COLLECTION_CHUNK = 75  # hard limit of /cards/collection


def _faces_text(card: dict[str, Any], field: str) -> str:
    if card.get(field):
        return card[field]
    faces = card.get("card_faces") or []
    return " // ".join(f.get(field, "") for f in faces if f.get(field))


def _image(card: dict[str, Any], size: str = "normal") -> str | None:
    if "image_uris" in card:
        return card["image_uris"].get(size)
    faces = card.get("card_faces") or []
    if faces and "image_uris" in faces[0]:
        return faces[0]["image_uris"].get(size)
    return None


def _back_image(card: dict[str, Any], size: str = "normal") -> str | None:
    """Image of the back face for double-faced cards (transform, MDFC, ...), else None."""
    faces = card.get("card_faces") or []
    if "image_uris" not in card and len(faces) > 1 and "image_uris" in faces[1]:
        return faces[1]["image_uris"].get(size)
    return None


def compact(card: dict[str, Any], *, with_text: bool = True) -> dict[str, Any]:
    """Reduce a Scryfall card object to what deckbuilding needs (keeps LLM context small)."""
    prices = card.get("prices") or {}
    out: dict[str, Any] = {
        "name": card.get("name"),
        "mana_cost": _faces_text(card, "mana_cost"),
        "cmc": card.get("cmc", 0),
        "type_line": _faces_text(card, "type_line"),
        "color_identity": card.get("color_identity", []),
        "commander_legal": (card.get("legalities") or {}).get("commander") == "legal",
        "game_changer": bool(card.get("game_changer", False)),
        "edhrec_rank": card.get("edhrec_rank"),
        "price_usd": prices.get("usd") or prices.get("usd_foil"),
        "price_eur": prices.get("eur") or prices.get("eur_foil"),
        "image": _image(card),
        "image_back": _back_image(card),
        "layout": card.get("layout"),
        "scryfall_uri": card.get("scryfall_uri"),
    }
    if "_query_name" in card:
        out["_query_name"] = card["_query_name"]
    if with_text:
        out["oracle_text"] = _faces_text(card, "oracle_text")
        if card.get("power") is not None:
            out["pt"] = f"{card.get('power')}/{card.get('toughness')}"
    return out


async def search(query: str, *, order: str = "edhrec", unique: str = "cards", max_results: int = 40) -> dict[str, Any]:
    """Full-text Scryfall search (https://scryfall.com/docs/syntax). Returns raw card objects."""
    cards: list[dict[str, Any]] = []
    total = 0
    url: str | None = f"{BASE}/cards/search"
    params: dict[str, Any] | None = {"q": query, "order": order, "unique": unique}
    try:
        while url and len(cards) < max_results:
            page = await get_json(url, params)
            total = page.get("total_cards", total)
            cards.extend(page.get("data", []))
            url = page.get("next_page") if page.get("has_more") else None
            params = None  # next_page already encodes the query
    except HttpError as exc:
        if exc.status == 404:  # Scryfall returns 404 for "no cards found"
            return {"total": 0, "cards": []}
        raise
    return {"total": total, "cards": cards[:max_results]}


async def named(name: str, *, fuzzy: bool = True) -> dict[str, Any]:
    return await get_json(f"{BASE}/cards/named", {"fuzzy" if fuzzy else "exact": name})


async def autocomplete(prefix: str) -> list[str]:
    data = await get_json(f"{BASE}/cards/autocomplete", {"q": prefix}, ttl=7 * 24 * 3600)
    return data.get("data", [])


def _matches(query: str, card: dict[str, Any]) -> bool:
    q = query.lower()
    return q == card.get("name", "").lower() or q in (f.get("name", "").lower() for f in card.get("card_faces") or [])


async def collection(names: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch many cards by name. Returns (found cards, names not found).

    Every returned card carries ``_query_name``: the name it was requested as (matters for typos
    and fuzzy matches, which Scryfall answers with the corrected name).
    """
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    unique_names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    for i in range(0, len(unique_names), COLLECTION_CHUNK):
        chunk = unique_names[i : i + COLLECTION_CHUNK]
        data = await post_json(f"{BASE}/cards/collection", {"identifiers": [{"name": n} for n in chunk]})
        cards = data.get("data", [])
        not_found = {ident.get("name", "").lower() for ident in data.get("not_found", [])}
        for query in chunk:
            if query.lower() in not_found:
                missing.append(query)
                continue
            card = next((c for c in cards if _matches(query, c)), None)
            if card is None:
                missing.append(query)
            else:
                found.append({**card, "_query_name": query})

    # Retry misses with fuzzy lookup (typos, missing punctuation, ...)
    still_missing = []
    for query in missing:
        try:
            found.append({**await named(query, fuzzy=True), "_query_name": query})
        except HttpError:
            still_missing.append(query)
    return found, still_missing


def image_url(scryfall_id: str, size: str = "normal") -> str:
    """Front image of a printing, derived from its Scryfall ID (no API call needed)."""
    return f"https://cards.scryfall.io/{size}/front/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.jpg"


def printing(card: dict[str, Any], *, foil: bool = False) -> dict[str, Any]:
    """The facts about one printing that a collection entry keeps (artwork, set, price)."""
    prices = card.get("prices") or {}
    eur = (prices.get("eur_foil") or prices.get("eur")) if foil else (prices.get("eur") or prices.get("eur_foil"))
    usd = (prices.get("usd_foil") or prices.get("usd")) if foil else (prices.get("usd") or prices.get("usd_foil"))
    return {
        "name": card.get("name"),
        "scryfall_id": card.get("id"),
        "set": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
        "lang": card.get("lang", "en"),
        "image": _image(card),
        "image_back": _back_image(card),
        "released": card.get("released_at"),
        "price_eur": eur,
        "price_usd": usd,
    }


async def by_identifiers(idents: list[dict[str, str]]) -> tuple[list[dict[str, Any] | None], int]:
    """Look up printings by ``{"id": …}`` or ``{"set": …, "collector_number": …}`` identifiers.

    Returns the raw cards in input order (``None`` where Scryfall has no match) and the miss count.
    """
    out: list[dict[str, Any] | None] = []
    for i in range(0, len(idents), COLLECTION_CHUNK):
        chunk = idents[i : i + COLLECTION_CHUNK]
        data = await post_json(f"{BASE}/cards/collection", {"identifiers": chunk})
        cards = data.get("data", [])
        by_id = {c.get("id"): c for c in cards}
        by_set = {(c.get("set", "").lower(), str(c.get("collector_number", "")).lower()): c for c in cards}
        for ident in chunk:
            if "id" in ident:
                out.append(by_id.get(ident["id"]))
            else:
                out.append(by_set.get((ident.get("set", "").lower(), str(ident.get("collector_number", "")).lower())))
    return out, sum(1 for c in out if c is None)


async def prints(name: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """All paper printings of a card (newest first) as ``printing()`` dicts."""
    result = await search(f'!"{name}" game:paper', order="released", unique="prints", max_results=limit)
    return [printing(c) for c in result["cards"]]


async def game_changers() -> list[str]:
    """Current official Game Changers list, as tagged by Scryfall."""
    result = await search("is:gamechanger", order="name", max_results=200)
    return sorted(c["name"] for c in result["cards"])
