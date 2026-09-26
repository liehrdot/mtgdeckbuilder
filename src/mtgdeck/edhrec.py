"""EDHREC data via its public JSON endpoints (json.edhrec.com). No key needed.

EDHREC has no official, documented API; these are the JSON files its website loads.
Structure may change, so parsing is defensive.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .http import HttpError, get_json

BASE = "https://json.edhrec.com/pages"
BUDGETS = {"budget", "expensive"}
# EDHREC filters decks by Commander Bracket: /pages/commanders/<slug>/<bracket>[/<budget>].json
BRACKET_SLUGS = {1: "exhibition", 2: "core", 3: "upgraded", 4: "optimized", 5: "cedh"}


def slugify(name: str) -> str:
    """EDHREC slug for a card name: 'Atraxa, Praetors' Voice' -> 'atraxa-praetors-voice'."""
    name = name.split(" // ")[0]  # double-faced cards use the front face
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"['’,.!?:\"]", "", name.lower())
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def commander_slug(commanders: list[str]) -> str:
    """Partner pairs are listed with both slugs in alphabetical order."""
    return "-".join(sorted(slugify(c) for c in commanders if c))


def _page_path(commanders: list[str], theme: str | None, budget: str | None, bracket: int | None = None) -> str:
    path = commander_slug(commanders)
    if theme:
        path += "/" + slugify(theme)
    if bracket:
        path += "/" + BRACKET_SLUGS[int(bracket)]
    if budget:
        if budget not in BUDGETS:
            raise ValueError(f"budget must be one of {sorted(BUDGETS)}")
        path += "/" + budget
    return path


def _cardlists(page: dict[str, Any]) -> list[dict[str, Any]]:
    return ((page.get("container") or {}).get("json_dict") or {}).get("cardlists") or []


def _round(x: Any, digits: int = 2) -> Any:
    return round(x, digits) if isinstance(x, (int, float)) else x


async def commander_page(
    commanders: list[str],
    *,
    theme: str | None = None,
    budget: str | None = None,
    bracket: int | None = None,
    per_category: int = 25,
) -> dict[str, Any]:
    """Card recommendations for a commander (optionally for a theme / bracket / budget variant)."""
    path = _page_path(commanders, theme, budget, bracket)
    try:
        page = await get_json(f"{BASE}/commanders/{path}.json")
    except HttpError as exc:
        if exc.status in (403, 404):
            raise LookupError(
                f"EDHREC has no page '{path}'. Check the commander name / theme slug "
                "(themes are listed in the 'themes' field of the plain commander page)."
            ) from exc
        raise

    categories = []
    for cl in _cardlists(page):
        cards = []
        for cv in (cl.get("cardviews") or [])[:per_category]:
            potential = cv.get("potential_decks") or 0
            num_decks = cv.get("num_decks") or cv.get("inclusion") or 0  # both are raw deck counts
            cards.append(
                {
                    "name": cv.get("name"),
                    "synergy": _round(cv.get("synergy")),
                    "inclusion_pct": _round(100 * num_decks / potential, 1) if potential else None,
                    "num_decks": num_decks,
                }
            )
        categories.append({"category": cl.get("header") or cl.get("tag"), "cards": cards})

    taglinks = (page.get("panels") or {}).get("taglinks") or []
    themes = [{"name": t.get("value"), "slug": t.get("slug"), "decks": t.get("count")} for t in taglinks]

    return {
        "page": f"https://edhrec.com/commanders/{path}",
        "num_decks": page.get("num_decks_avg") or page.get("num_decks"),
        "themes": themes[:30],
        "categories": categories,
    }


async def average_deck(
    commanders: list[str], *, budget: str | None = None, bracket: int | None = None
) -> dict[str, Any]:
    """EDHREC 'average deck' for a commander as a list of 'N Card Name' lines (incl. basics)."""
    path = _page_path(commanders, None, budget, bracket)
    try:
        page = await get_json(f"{BASE}/average-decks/{path}.json")
    except HttpError as exc:
        if exc.status in (403, 404):
            raise LookupError(f"EDHREC has no average deck for '{path}'.") from exc
        raise
    deck = page.get("deck")
    if not deck:
        # Fallback: rebuild from the card lists
        deck = [f"1 {cv['name']}" for cl in _cardlists(page) for cv in cl.get("cardviews", []) if cv.get("name")]
    return {"page": f"https://edhrec.com/average-decks/{path}", "deck": deck}
