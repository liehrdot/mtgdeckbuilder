"""Commander Spellbook (https://commanderspellbook.com) combo database. No key needed.

API source: https://github.com/SpaceCowMedia/commander-spellbook-backend
"""

from __future__ import annotations

from typing import Any

from .http import HttpError, post_json

BASE = "https://backend.commanderspellbook.com"

# bracketTag of a combo variant
BRACKET_TAGS = {
    "E": "Exhibition",
    "C": "Core",
    "O": "Oddball",
    "P": "Powerful",
    "S": "Spicy",
    "R": "Ruthless",
    "B": "Banned",
}
# Spellbook's deck-level estimate -> official bracket number (approximate mapping)
BRACKET_TAG_TO_NUMBER = {"E": 1, "C": 2, "O": 3, "P": 3, "S": 4, "R": 4, "B": 5}


def _deck_body(commanders: list[str], cards: list[str]) -> dict[str, Any]:
    return {
        "commanders": [{"card": c, "quantity": 1} for c in commanders],
        "main": [{"card": c, "quantity": 1} for c in cards],
    }


def _variant(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": v.get("id"),
        "cards": [((u.get("card") or {}).get("name")) for u in v.get("uses") or []],
        "requires": [((r.get("template") or {}).get("name")) for r in v.get("requires") or []],
        "produces": [((p.get("feature") or {}).get("name")) for p in v.get("produces") or []],
        "bracket_tag": v.get("bracketTag"),
        "bracket_tag_name": BRACKET_TAGS.get(v.get("bracketTag") or "", None),
        "mana_needed": v.get("manaNeeded") or None,
        "identity": v.get("identity"),
        "popularity": v.get("popularity"),
        "url": f"https://commanderspellbook.com/combo/{v.get('id')}/" if v.get("id") else None,
        "steps": (v.get("description") or "")[:600],
    }


async def find_combos(commanders: list[str], cards: list[str], *, almost_limit: int = 15) -> dict[str, Any]:
    """Combos fully contained in the deck, plus combos that miss exactly one card."""
    data = await post_json(f"{BASE}/find-my-combos", _deck_body(commanders, cards), ttl=3600)
    results = data.get("results") or {}
    included = [_variant(v) for v in results.get("included") or []]
    almost = sorted(results.get("almostIncluded") or [], key=lambda v: -(v.get("popularity") or 0))
    return {
        "identity": results.get("identity"),
        "included": included,
        "almost_included": [_variant(v) for v in almost[:almost_limit]],
    }


async def estimate_bracket(commanders: list[str], cards: list[str]) -> dict[str, Any]:
    """Spellbook's bracket estimate: flags game changers, MLD, extra turns and relevant combos."""
    try:
        data = await post_json(f"{BASE}/estimate-bracket", _deck_body(commanders, cards), ttl=3600)
    except HttpError as exc:
        return {"error": str(exc)}

    flagged: dict[str, list[str]] = {"game_changers": [], "mass_land_denial": [], "extra_turns": [], "banned": []}
    for entry in data.get("cards") or []:
        card = entry.get("card")
        name = card.get("name") if isinstance(card, dict) else card
        for key, field in (("game_changers", "gameChanger"), ("mass_land_denial", "massLandDenial"),
                           ("extra_turns", "extraTurn"), ("banned", "banned")):  # fmt: skip
            if entry.get(field):
                flagged[key].append(name)

    combos = []
    for entry in data.get("combos") or []:
        combo = entry.get("combo") or {}
        variant = _variant(combo) if isinstance(combo, dict) else {"id": combo}
        variant.update(
            {
                "two_card": bool(entry.get("definitelyTwoCard") or entry.get("arguablyTwoCard")),
                "definitely_two_card": bool(entry.get("definitelyTwoCard")),
                "speed": entry.get("speed"),
                "relevant": bool(entry.get("relevant")),
                "lock": bool(entry.get("lock")),
                "mass_land_denial": bool(entry.get("massLandDenial")),
                "extra_turn": bool(entry.get("extraTurn")),
            }
        )
        combos.append(variant)

    tag = data.get("bracketTag")
    return {
        "bracket_tag": tag,
        "bracket_tag_name": BRACKET_TAGS.get(tag or ""),
        "estimated_bracket": BRACKET_TAG_TO_NUMBER.get(tag or ""),
        **flagged,
        "combos": combos,
    }
