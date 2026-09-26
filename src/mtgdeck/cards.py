"""Resolve card names to compact card data: local bulk DB first, Scryfall API as fallback."""

from __future__ import annotations

from typing import Any

from . import carddb, scryfall
from .deck import card_roles


async def resolve(names: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[str]]:
    """Return (cards by English name, renames {query -> English name}, names not found).

    Names in other languages (e.g. German) resolve via the local DB built from Scryfall's
    All Cards bulk file.
    """
    queries = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    found: list[dict[str, Any]] = []
    missing = queries
    if carddb.available():
        found, missing = carddb.lookup(queries)
        await _complete_double_faced(found)
    if missing:
        raw, missing = await scryfall.collection(missing)
        found += [scryfall.compact(c) for c in raw]

    by_name: dict[str, dict[str, Any]] = {}
    renames: dict[str, str] = {}
    for c in found:
        query = c.pop("_query_name", c["name"])
        if query != c["name"]:
            renames[query] = c["name"]
        c["roles"] = card_roles(c)
        by_name[c["name"]] = c
    return by_name, renames, missing


async def _complete_double_faced(cards: list[dict[str, Any]]) -> None:
    """Cards from a DB built before layout/image_back existed: fetch those fields live for
    multi-face cards (otherwise their back faces would be missing in previews and prints)."""
    stale = [c for c in cards if " // " in c.get("name", "") and "layout" not in c]
    if not stale:
        return
    try:
        raw, _ = await scryfall.collection([c["name"] for c in stale])
    except Exception:  # offline: keep what we have
        return
    fresh = {r["name"]: scryfall.compact(r) for r in raw}
    for c in stale:
        if f := fresh.get(c["name"]):
            c.update({k: f[k] for k in ("layout", "image", "image_back") if k in f})
