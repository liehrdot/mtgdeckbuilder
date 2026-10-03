"""Fine-grained power level *within* the Commander Brackets.

Two parts:

- ``PowerProfile``: what the player wants – a sub-tier inside the bracket ("lower 4", "upper 3"),
  optional house rules that are *stricter* than the bracket (e.g. bracket 4 without Game
  Changers, bracket 3 with max. 2), and a style/vibe ("witzig", "politisch").
- ``score()``: a transparent heuristic power score on the 1.0-5.99 bracket scale
  (e.g. 3.8 = upper bracket 3), built from Game Changers, tutors, fast mana, free interaction,
  combos, curve and interaction density. It is an estimate to steer tuning, not an official rating.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import fmt

Tier = Literal["low", "mid", "high"]
TIER_LABELS = {"low": "unteres", "mid": "mittleres", "high": "oberes"}
TIER_CENTER = {"low": 0.17, "mid": 0.5, "high": 0.83}
TIER_ORDER: list[Tier] = ["low", "mid", "high"]


class PowerProfile(BaseModel):
    """Player's power wishes for a deck. None = use the bracket's own rule."""

    tier: Tier | None = Field(None, description="Sub-tier inside the bracket: low / mid / high")
    max_game_changers: int | None = Field(None, ge=0, description="House rule, only stricter than the bracket")
    max_tutors: int | None = Field(None, ge=0, description="Max. non-land tutors (house rule)")
    allow_two_card_combos: bool | None = Field(None, description="False = no 2-card combos at all")
    allow_extra_turns: bool | None = Field(None, description="False = no extra turn cards")
    allow_mass_land_denial: bool | None = Field(None, description="False = no mass land denial")
    style: str = Field("", description="Vibe of the deck, e.g. 'witzig', 'chaotisch', 'politisch', 'thematisch'")
    notes: str = Field("", description="Free-form wishes for the power level")

    def is_empty(self) -> bool:
        return self == PowerProfile()


def label(value: float) -> dict[str, Any]:
    """3.8 -> {'bracket': 3, 'tier': 'high', 'text': 'oberes Bracket 3'}"""
    value = min(max(value, 1.0), 5.99)
    bracket = int(value)
    frac = value - bracket
    tier: Tier = "low" if frac < 1 / 3 else "mid" if frac < 2 / 3 else "high"
    return {"value": round(value, 1), "bracket": bracket, "tier": tier, "text": f"{TIER_LABELS[tier]} Bracket {bracket}"}


def target_value(bracket: int, tier: Tier | None) -> float:
    return bracket + TIER_CENTER[tier or "mid"]


def step(bracket: int, tier: Tier | None, direction: int) -> tuple[int, Tier]:
    """One sub-tier up (+1) or down (-1): 3/high -> 4/low, 4/low -> 3/high."""
    idx = bracket * 3 + TIER_ORDER.index(tier or "mid") + direction
    idx = min(max(idx, 3), 5 * 3 + 2)
    return idx // 3, TIER_ORDER[idx % 3]


# --- heuristic score --------------------------------------------------------------------------

_ADDS_MANA_RE = re.compile(r"\badd\b[^.]*(\{[WUBRGC]\}|mana)", re.I)
_BIG_MANA_RE = re.compile(r"add \{C\}\{C\}|add (two|three) mana|add \{[WUBRGC]\}\{[WUBRGC]\}", re.I)
_FREE_SPELL_RE = re.compile(r"rather than pay this spell'?s mana cost|without paying (its|their) mana cost", re.I)
_INTERACTION = {"removal", "counterspell", "board_wipe"}


def _is_fast_mana(card: dict[str, Any]) -> bool:
    """Mana rocks/lands that accelerate beyond normal ramp: 0-1 MV mana sources, 2 MV sources
    of two+ mana, and lands tapping for {C}{C} (Sol Ring, Mana Crypt, Grim Monolith, Ancient Tomb ...)."""
    type_line = (card.get("type_line") or "").split("//")[0]
    text = card.get("oracle_text") or ""
    if "Creature" in type_line or "Basic" in type_line:
        return False
    if "Land" in type_line:
        return bool(re.search(r"add \{C\}\{C\}", text, re.I))
    if not _ADDS_MANA_RE.search(text):
        return False
    cmc = card.get("cmc") or 0
    return cmc <= 1 or (cmc <= 2 and bool(_BIG_MANA_RE.search(text)))


def score(
    cards: list[dict[str, Any]],
    *,
    game_changers: list[str],
    tutors: list[str],
    two_card_combos: list[dict[str, Any]],
    extra_turns: list[str],
    mass_land_denial: list[str],
    min_bracket: int = 1,
) -> dict[str, Any]:
    """Heuristic power estimate. ``cards`` = compact cards incl. roles (commanders included)."""
    nonland = [c for c in cards if "Land" not in (c.get("type_line") or "").split("//")[0]]
    avg_cmc = sum(c.get("cmc") or 0 for c in nonland) / len(nonland) if nonland else 3.0
    gc = set(game_changers)
    fast_mana = sorted(c["name"] for c in cards if _is_fast_mana(c) and c["name"] not in gc)
    free_spells = sorted(c["name"] for c in cards if _FREE_SPELL_RE.search(c.get("oracle_text") or "") and c["name"] not in gc)
    other_tutors = [t for t in tutors if t not in gc]
    interaction = sum(1 for c in cards if _INTERACTION & set(c.get("roles") or []))

    parts: list[tuple[str, float]] = [("Basis", 1.6)]
    if gc:
        parts.append((f"{len(gc)} Game Changer", min(0.35 * len(gc), 1.6)))
    if other_tutors:
        parts.append((f"{len(other_tutors)} Tutoren", min(0.12 * len(other_tutors), 0.6)))
    if fast_mana:
        parts.append((f"{len(fast_mana)} Fast Mana", min(0.2 * len(fast_mana), 0.8)))
    if free_spells:
        parts.append((f"{len(free_spells)} Free Spells", min(0.2 * len(free_spells), 0.6)))
    if two_card_combos:
        parts.append((f"{len(two_card_combos)} 2-Karten-Combo(s)", min(0.5 + 0.25 * (len(two_card_combos) - 1), 1.1)))
    if extra_turns:
        parts.append((f"{len(extra_turns)} Extra-Züge", min(0.1 * len(extra_turns), 0.3)))
    if mass_land_denial:
        parts.append(("Massen-Landzerstörung", 0.3))
    if avg_cmc < 2.5:
        parts.append((f"sehr niedrige Kurve (Ø {fmt.num(avg_cmc)})", 0.5))
    elif avg_cmc < 3.0:
        parts.append((f"niedrige Kurve (Ø {fmt.num(avg_cmc)})", 0.25))
    elif avg_cmc > 3.8:
        parts.append((f"hohe Kurve (Ø {fmt.num(avg_cmc)})", -0.3))
    if interaction >= 14:
        parts.append((f"viel Interaktion ({interaction})", 0.25))
    elif interaction < 6:
        parts.append((f"wenig Interaktion ({interaction})", -0.2))

    raw = sum(v for _, v in parts)
    value = max(raw, float(min_bracket))  # hard rules (e.g. 4+ Game Changers) set a floor
    return {
        **label(value),
        "components": [{"reason": r, "points": round(v, 2)} for r, v in parts],
        "floor_from_rules": min_bracket if min_bracket > raw else None,
        "fast_mana": fast_mana,
        "free_spells": free_spells,
        "note": "Heuristische Einschätzung – Tempo, Konsistenz und Siegpläne im Deck-Text begründen.",
    }


def check_profile(
    profile: PowerProfile | None,
    bracket_rules: dict[str, Any],
    *,
    game_changers: list[str],
    tutors: list[str],
    two_card_combos: list[dict[str, Any]],
    extra_turns: list[str],
    mass_land_denial: list[str],
    power: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """House rules -> (violations, warnings)."""
    if not profile:
        return [], []
    violations: list[str] = []
    warnings: list[str] = []
    n = bracket_rules["number"]

    bmax = bracket_rules["max_game_changers"]
    if profile.max_game_changers is not None:
        if bmax is not None and profile.max_game_changers > bmax:
            warnings.append(f"Vorgabe max. {profile.max_game_changers} Game Changer ist lockerer als Bracket {n} ({bmax}) – die Bracket-Regel gilt")
        elif len(game_changers) > profile.max_game_changers:
            violations.append(f"Eigene Vorgabe: max. {profile.max_game_changers} Game Changer, im Deck {len(game_changers)}: {', '.join(game_changers)}")
    if profile.max_tutors is not None and len(tutors) > profile.max_tutors:
        violations.append(f"Eigene Vorgabe: max. {profile.max_tutors} Tutoren, im Deck {len(tutors)}: {', '.join(tutors)}")
    if profile.allow_two_card_combos is False and two_card_combos:
        names = "; ".join(" + ".join(c.get("cards") or ["?"]) for c in two_card_combos)
        violations.append(f"Eigene Vorgabe: keine 2-Karten-Combos – gefunden: {names}")
    if profile.allow_extra_turns is False and extra_turns:
        violations.append(f"Eigene Vorgabe: keine Extra-Züge – gefunden: {', '.join(extra_turns)}")
    if profile.allow_mass_land_denial is False and mass_land_denial:
        violations.append(f"Eigene Vorgabe: keine Massen-Landzerstörung – gefunden: {', '.join(mass_land_denial)}")
    for flag, allowed_by_bracket, what in (
        (profile.allow_two_card_combos, bracket_rules["two_card_combos"] != "none", "2-Karten-Combos"),
        (profile.allow_extra_turns, bracket_rules["extra_turns"] != "none", "Extra-Züge"),
        (profile.allow_mass_land_denial, bracket_rules["mass_land_denial"], "Massen-Landzerstörung"),
    ):
        if flag is True and not allowed_by_bracket:
            warnings.append(f"{what}: in Bracket {n} nicht erlaubt – die Vorgabe wird ignoriert")

    if profile.tier:
        target = target_value(n, profile.tier)
        diff = power["value"] - target
        if abs(diff) > 0.4:
            wanted = f"{TIER_LABELS[profile.tier]} Bracket {n}"
            direction = "stärker" if diff > 0 else "schwächer"
            warnings.append(f"Deck wirkt {direction} als gewünscht: eingeschätzt {power['text']} ({power['value']}), Ziel {wanted}")
    return violations, warnings
