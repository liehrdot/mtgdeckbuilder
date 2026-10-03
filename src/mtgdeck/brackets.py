"""Official Commander Brackets (Wizards of the Coast, beta).

State: Oct 21 2025 rework (brackets defined by expected earliest game end, tutor limits removed)
plus the Feb 9 2026 Game Changer update. The Game Changer *list* is not hardcoded: it is read
from Scryfall's ``game_changer`` flag (``is:gamechanger``), so it stays current.
"""

from __future__ import annotations

import re
from typing import Any

from . import power as power_mod
from .power import PowerProfile

BRACKETS: list[dict[str, Any]] = [
    {
        "number": 1,
        "name": "Exhibition",
        "earliest_game_end": "Zug 9+",
        "max_game_changers": 0,
        "mass_land_denial": False,
        "extra_turns": "none",
        "two_card_combos": "none",
        "summary": "Thema und Spaß vor Stärke. Keine Game Changer, keine Extra-Züge, keine 2-Karten-Combos, keine Massen-Landzerstörung.",
    },
    {
        "number": 2,
        "name": "Core",
        "earliest_game_end": "Zug 8+",
        "max_game_changers": 0,
        "mass_land_denial": False,
        "extra_turns": "few",
        "two_card_combos": "none",
        "summary": "Niveau eines Precons. Keine Game Changer, keine 2-Karten-Combos, keine Massen-Landzerstörung, Extra-Züge nur wenige und nicht hintereinander.",
    },
    {
        "number": 3,
        "name": "Upgraded",
        "earliest_game_end": "Zug 6+",
        "max_game_changers": 3,
        "mass_land_denial": False,
        "extra_turns": "few",
        "two_card_combos": "late",
        "summary": "Gestärkte Decks. Bis zu 3 Game Changer, keine Massen-Landzerstörung, keine frühen 2-Karten-Combos, Extra-Züge nicht hintereinander.",
    },
    {
        "number": 4,
        "name": "Optimized",
        "earliest_game_end": "Zug 4+",
        "max_game_changers": None,
        "mass_land_denial": True,
        "extra_turns": "any",
        "two_card_combos": "any",
        "summary": "Hohe Power ohne Einschränkungen außer der Bannliste. Tödlich und effizient.",
    },
    {
        "number": 5,
        "name": "cEDH",
        "earliest_game_end": "jederzeit",
        "max_game_changers": None,
        "mass_land_denial": True,
        "extra_turns": "any",
        "two_card_combos": "any",
        "summary": "Competitive EDH: maximal optimiert auf das kompetitive Meta.",
    },
]
BY_NUMBER = {b["number"]: b for b in BRACKETS}

# Fallback when Commander Spellbook is unreachable. Commonly cited mass land denial cards.
MASS_LAND_DENIAL = {
    "Armageddon", "Ravages of War", "Catastrophe", "Jokulhaups", "Obliterate", "Decree of Annihilation",
    "Sunder", "Ruination", "Wildfire", "Destructive Force", "Global Ruin", "Impending Disaster",
    "Boom // Bust", "Death Cloud", "Epicenter", "Keldon Firebombers", "Burning of Xinye", "Devastation",
    "Winter Orb", "Static Orb", "Stasis", "Rising Waters", "Hokori, Dust Drinker", "Back to Basics",
    "Blood Moon", "Magus of the Moon", "Contamination", "Infernal Darkness", "Tsabo's Decree",
    "Mana Vortex", "Worldfire", "Acid Rain", "Flashfires", "Tsunami",
    "Boil", "Choke", "Rolling Earthquake", "Hall of Gemstone", "Armageddon Clock", "Winter Moon",
    "Thoughts of Ruin", "Numot, the Devastator", "Sire of Ruin", "Tectonic Break", "Cataclysm",
    "Myojin of Infinite Rage", "Desolation", "Dimensional Breach",
}  # fmt: skip
_MLD_RE = re.compile(
    r"destroy all (nonbasic )?lands|each player sacrifices (all|\w+) lands|lands don'?t untap|"
    r"nonbasic lands are (mountains|islands|plains|swamps|forests)|exile all lands|return all lands",
    re.I,
)
_EXTRA_TURN_RE = re.compile(r"takes? (an|\w+) extra turns?", re.I)


def describe() -> list[dict[str, Any]]:
    return BRACKETS


def evaluate(
    target: int,
    cards: list[dict[str, Any]],
    *,
    spellbook_estimate: dict[str, Any] | None = None,
    two_card_combos: list[dict[str, Any]] | None = None,
    profile: PowerProfile | None = None,
) -> dict[str, Any]:
    """Check compact cards (incl. commanders) against the target bracket and the player's
    power profile (sub-tier + house rules), and estimate the power level inside the brackets."""
    rules = BY_NUMBER[target]
    names = {c["name"] for c in cards}
    sb = spellbook_estimate if spellbook_estimate and not spellbook_estimate.get("error") else None

    game_changers = sorted({c["name"] for c in cards if c.get("game_changer")} | set((sb or {}).get("game_changers", [])))
    mld = {c["name"] for c in cards if c["name"] in MASS_LAND_DENIAL or _MLD_RE.search(c.get("oracle_text") or "")}
    mld |= set((sb or {}).get("mass_land_denial", []))
    extra_turns = {c["name"] for c in cards if _EXTRA_TURN_RE.search(c.get("oracle_text") or "")}
    extra_turns |= set((sb or {}).get("extra_turns", []))
    tutors = sorted(c["name"] for c in cards if "tutor" in (c.get("roles") or []))

    combos = []
    if sb:
        combos = [c for c in sb.get("combos", []) if c.get("two_card")]
    elif two_card_combos:
        combos = two_card_combos
    combos = [c for c in combos if set(c.get("cards") or []) <= names or not c.get("cards")]

    violations: list[str] = []
    warnings: list[str] = []
    max_gc = rules["max_game_changers"]
    if max_gc is not None and len(game_changers) > max_gc:
        violations.append(f"{len(game_changers)} Game Changer (erlaubt: {max_gc}): {', '.join(game_changers)}")
    if not rules["mass_land_denial"] and mld:
        violations.append(f"Massen-Landzerstörung ist in Bracket {target} nicht erlaubt: {', '.join(sorted(mld))}")
    if rules["extra_turns"] == "none" and extra_turns:
        violations.append(f"Extra-Züge sind in Bracket {target} nicht erlaubt: {', '.join(sorted(extra_turns))}")
    elif rules["extra_turns"] == "few" and extra_turns:
        if len(extra_turns) > 3:
            violations.append(f"Zu viele Extra-Zug-Karten ({len(extra_turns)}) für Bracket {target}")
        else:
            warnings.append(f"Extra-Züge erlaubt, aber nicht hintereinander oder wiederholt: {', '.join(sorted(extra_turns))}")
    if combos:
        combo_names = "; ".join(" + ".join(c.get("cards") or ["?"]) for c in combos)
        if rules["two_card_combos"] == "none":
            violations.append(f"2-Karten-Combos sind in Bracket {target} nicht erlaubt: {combo_names}")
        elif rules["two_card_combos"] == "late":
            warnings.append(f"2-Karten-Combos in Bracket 3 nur, wenn sie nicht früh im Spiel gewinnen: {combo_names}")

    estimated = (sb or {}).get("estimated_bracket")
    if estimated is None:
        estimated = _heuristic_estimate(len(game_changers), bool(mld), len(extra_turns), bool(combos))

    hard_floor = 4 if len(game_changers) > 3 or mld else 3 if game_changers or combos else 1
    facts = {
        "game_changers": game_changers,
        "tutors": tutors,
        "two_card_combos": combos,
        "extra_turns": sorted(extra_turns),
        "mass_land_denial": sorted(mld),
    }
    power = power_mod.score(cards, **facts, min_bracket=hard_floor)
    pv, pw = power_mod.check_profile(profile, rules, **facts, power=power)
    violations += pv
    warnings += pw

    return {
        "target": target,
        "target_name": rules["name"],
        "compliant": not violations,
        "violations": violations,
        "warnings": warnings,
        "game_changers": game_changers,
        "mass_land_denial": sorted(mld),
        "extra_turns": sorted(extra_turns),
        "tutors": tutors,
        "two_card_combos": [
            {"cards": c.get("cards"), "produces": c.get("produces"), "url": c.get("url")} for c in combos
        ],
        "estimated": estimated,
        "estimate_source": "Commander Spellbook" if sb and sb.get("estimated_bracket") else "Heuristik",
        "spellbook_tag": (sb or {}).get("bracket_tag_name"),
        "power": power,
        "profile": profile.model_dump(exclude_defaults=True) if profile else None,
        "target_text": (
            f"{power_mod.TIER_LABELS[profile.tier]} Bracket {target}" if profile and profile.tier else f"Bracket {target}"
        ),
    }


def _heuristic_estimate(n_gc: int, mld: bool, n_extra: int, combos: bool) -> int:
    if n_gc > 3 or mld or combos or n_extra > 3:
        return 4
    if n_gc > 0:
        return 3
    if n_extra:
        return 2
    return 2  # "1" is about intent/theme, not detectable from the list
