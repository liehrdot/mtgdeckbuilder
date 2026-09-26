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
