"""Full deck check: Commander legality, color identity, singleton, card count, roles, bracket."""

from __future__ import annotations

import re
from typing import Any

import httpx

from . import blacklist, brackets, spellbook
from .cards import resolve
from .deck import BASIC_LANDS, DeckEntry, deck_stats, parse_decklist
from .http import HttpError
from .power import PowerProfile

_ANY_NUMBER_RE = re.compile(r"a deck can have (any number of|up to \w+) cards? named", re.I)
_PARTNER_RE = re.compile(
    r"\bpartner\b|friends forever|choose a background|doctor'?s companion|can be your commander", re.I
)

# Recommended minimums for a functional Commander deck (warnings only).
ROLE_MINIMUMS = {"ramp": 8, "card_draw": 8, "removal": 6, "board_wipe": 1}
ROLE_LABELS = {"ramp": "Ramp", "card_draw": "Kartenzug", "removal": "Removal", "board_wipe": "Board Wipes"}
LAND_RANGE = (33, 40)


def _fits_identity(card_ci: list[str], allowed: set[str]) -> bool:
    return set(card_ci) <= allowed


async def validate_deck(
    commanders: list[str],
    cards: list[str],
    bracket: int,
    *,
    currency: str = "eur",
    use_spellbook: bool = True,
    budget: float | None = None,
    proxy: bool = False,
    profile: PowerProfile | None = None,
) -> dict[str, Any]:
    """Validate a deck. ``cards`` are decklist lines ('1 Sol Ring' or 'Sol Ring'), without commanders.

    ``budget`` is the max. total price (warning when exceeded); ``proxy=True`` means the deck will
    be printed as proxies, so prices are informational only. Blacklisted cards are errors.
    """
    parsed = parse_decklist(cards)
    commanders = [c for c in commanders if c] + parsed.commanders
    entries = parsed.entries

    card_data, renames, not_found = await resolve(commanders + [e.name for e in entries])
    commanders = [renames.get(c, c) for c in commanders]
    merged: dict[str, int] = {}
    for e in entries:
        name = renames.get(e.name, e.name)
        merged[name] = merged.get(name, 0) + e.qty
    entries = [DeckEntry(n, q) for n, q in merged.items()]

    errors: list[str] = []
    warnings: list[str] = []

    # --- commanders
    if not 1 <= len(commanders) <= 2:
        errors.append(f"Es muss 1 Commander (oder 2 mit Partner/Background) geben, gefunden: {len(commanders)}")
    identity: set[str] = set()
    for name in commanders:
        c = card_data.get(name)
        if not c:
            continue
        identity |= set(c.get("color_identity") or [])
        front_type = (c.get("type_line") or "").split("//")[0]
        text = c.get("oracle_text") or ""
        is_legal_cmdr = ("Legendary" in front_type and "Creature" in front_type) or "can be your commander" in text
        if not is_legal_cmdr and "Background" not in front_type:
            errors.append(f"{name} kann kein Commander sein")
        if not c.get("commander_legal"):
            errors.append(f"{name} ist im Commander-Format gebannt")
        if len(commanders) == 2 and not (_PARTNER_RE.search(text) or "Background" in front_type):
            errors.append(f"{name} hat kein Partner/Background/Friends forever – zwei Commander nicht erlaubt")
    for m in set(commanders) & set(merged):
        errors.append(f"{m} steht sowohl als Commander als auch in der Hauptliste")

    # --- card pool
    total = sum(e.qty for e in entries) + len(commanders)
    if total != 100:
        errors.append(f"Das Deck hat {total} Karten (inkl. Commander), benötigt werden genau 100")
    if not_found:
        errors.append(f"Karten nicht gefunden (Tippfehler?): {', '.join(not_found)}")

    for e in entries:
        c = card_data.get(e.name)
        if not c:
            continue
        if not c.get("commander_legal"):
            errors.append(f"{e.name} ist in Commander nicht legal (gebannt oder nicht turnierlegal)")
        if identity and not _fits_identity(c.get("color_identity") or [], identity):
            errors.append(
                f"{e.name} ({''.join(c.get('color_identity') or [])}) liegt außerhalb der Farbidentität "
                f"{''.join(sorted(identity)) or 'farblos'}"
            )
        if e.qty > 1 and e.name not in BASIC_LANDS and not _ANY_NUMBER_RE.search(c.get("oracle_text") or ""):
            errors.append(f"{e.name} ist {e.qty}× enthalten (Singleton!)")

    banned_by_user = blacklist.names_lower()
    hits = [n for n in [*commanders, *merged] if n.lower() in banned_by_user]
    if hits:
        errors.append(f"Karten auf deiner Blacklist: {', '.join(hits)} – bitte ersetzen")

    # --- composition
    main_cards = [card_data[e.name] for e in entries if e.name in card_data]
    qty = {e.name: e.qty for e in entries}
    stats = deck_stats(main_cards, qty, currency)
    lands = stats["types"].get("Land", 0)
    if not LAND_RANGE[0] <= lands <= LAND_RANGE[1]:
        warnings.append(f"{lands} Länder – üblich sind {LAND_RANGE[0]}–{LAND_RANGE[1]} (abhängig von Kurve und Ramp)")
    price = stats[f"total_price_{currency}"] + sum(
        float(card_data[c].get(f"price_{currency}") or 0) for c in commanders if c in card_data
    )
    if budget and not proxy and price > budget:
        warnings.append(f"Budget überschritten: {price:.2f} {currency.upper()} > {budget:g} {currency.upper()}")
    for role, minimum in ROLE_MINIMUMS.items():
        have = stats["role_counts"].get(role, 0)
        if have < minimum:
            warnings.append(f"Nur {have}× {ROLE_LABELS[role]} erkannt (Richtwert ≥ {minimum}, Heuristik)")

    # --- bracket
    names_main = [e.name for e in entries]
    sb_estimate: dict[str, Any] | None = None
    combos_found: dict[str, Any] | None = None
    if use_spellbook:
        try:
            sb_estimate = await spellbook.estimate_bracket(commanders, names_main)
            if sb_estimate.get("error"):
                warnings.append(f"Commander Spellbook Bracket-Schätzung nicht verfügbar: {sb_estimate['error']}")
            combos_found = await spellbook.find_combos(commanders, names_main)
        except (HttpError, httpx.HTTPError, OSError) as exc:
            warnings.append(f"Commander Spellbook nicht erreichbar: {exc}")
    two_card = [c for c in (combos_found or {}).get("included", []) if len(c.get("cards") or []) == 2]
    all_cards = [card_data[n] for n in commanders if n in card_data] + main_cards
    bracket_result = brackets.evaluate(
        bracket, all_cards, spellbook_estimate=sb_estimate, two_card_combos=two_card, profile=profile
    )

    return {
        "legal": not errors,
        "errors": errors,
        "warnings": warnings,
        "bracket": bracket_result,
        "combos": {
            "included": (combos_found or {}).get("included", []),
            "almost_included": (combos_found or {}).get("almost_included", []),
        },
        "stats": stats,
        "color_identity": "".join(c for c in "WUBRG" if c in identity),
        "price_total": round(price, 2),
        "budget": None if proxy else budget,
        "proxy": proxy,
        "renamed": renames,
        "commanders": commanders,
        "cards": [{"name": e.name, "qty": e.qty} for e in entries],
        "_card_data": card_data,
    }
