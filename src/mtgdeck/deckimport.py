"""Turn an imported list (link, pasted text, precon) into a saved deck: preview with commander
candidates, then validation (bracket estimated when not given), categories and a new version."""

from __future__ import annotations

from typing import Any

from . import storage
from .cards import resolve
from .deck import BASIC_LANDS, parse_decklist
from .edhrec import slugify


def _can_be_commander(card: dict[str, Any]) -> bool:
    front = (card.get("type_line") or "").split("//")[0]
    text = (card.get("oracle_text") or "").lower()
    return ("Legendary" in front and ("Creature" in front or "Background" in front)) or "can be your commander" in text


async def preview(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the imported names (German too), count cards and offer commander candidates.

    Adds ``card_count``, ``unresolved``, ``commander_candidates`` ([{name, image}], legendary
    cards of the list) and ``suggested`` (commanders to preselect)."""
    parsed = parse_decklist(data["cards"])
    names = list(data["commanders"]) + [e.name for e in parsed.entries]
    try:
        found, renames, missing = await resolve(names)
    except Exception:  # offline: keep the names as they are
        found, renames, missing = {}, {}, []
    commanders = [renames.get(c, c) for c in data["commanders"]]
    counts: dict[str, int] = {}
    for e in parsed.entries:
        n = renames.get(e.name, e.name)
        counts[n] = counts.get(n, 0) + e.qty
    categories = {renames.get(k, k): v for k, v in (data.get("categories") or {}).items()}
    candidates = [{"name": n, "image": found[n].get("image")} for n in counts if n in found and _can_be_commander(found[n])]
    suggested = commanders
    hint = (data.get("commander_hint") or "").strip()
    if not suggested and hint:
        suggested = [c["name"] for c in candidates if c["name"] == hint or slugify(c["name"]) == slugify(hint)][:1]
    if not suggested and len(candidates) == 1:
        suggested = [candidates[0]["name"]]
    images = {n: c.get("image") for n, c in found.items()}
    return {**data, "commanders": commanders, "cards": [f"{q} {n}" for n, q in counts.items()], "categories": categories,
            "card_count": len(commanders) + sum(counts.values()), "unresolved": missing,
            "commander_candidates": candidates, "suggested": suggested,
            "commander_images": {c: images.get(c) for c in commanders}}  # fmt: skip


async def save(*, name: str, commanders: list[str], cards: list[str], categories: dict[str, str] | None = None,
               bracket: int | None = None, currency: str = "eur", description: str = "", note: str = "",
               extra: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """Validate and save as a new deck. ``cards`` are "N Name" lines without the commanders; with
    ``bracket=None`` the deck is checked against the bracket Spellbook/the heuristic estimates."""
    from .deckedit import guess_category  # deckedit imports validate; keep the import lazy
    from .validate import validate_deck

    if not commanders:
        raise ValueError("Wähle den Commander des Decks.")
    lines = [line for line in cards if line.split(" ", 1)[-1] not in commanders]
    target = bracket or 3
    result = await validate_deck(commanders, lines, target, currency=currency)
    if bracket is None:
        est = result["bracket"].get("estimated")
        if isinstance(est, int) and 1 <= est <= 5 and est != target:
            target = est
            result = await validate_deck(commanders, lines, target, currency=currency)
    card_data = result.pop("_card_data")
    cats = categories or {}

    def category(n: str) -> str:
        if n in BASIC_LANDS:
            return "Land"
        return cats.get(n) or guess_category(card_data.get(n, {}))

    deck = {
        "name": name, "slug": storage.unique_slug(name), "commanders": result["commanders"], "bracket": target,
        "description": description, "strategy": "", "budget": None, "proxy": False, "power_profile": None,
        "currency": currency, "notes": "", "change_note": note or "Importiert",
        "cards": [{**c, "category": category(c["name"])} for c in result.pop("cards")], "validation": result,
        **(extra or {}),
    }  # fmt: skip
    saved = storage.save(deck)
    return {**saved, "bracket": target, "legal": result["legal"], "errors": result["errors"]}
