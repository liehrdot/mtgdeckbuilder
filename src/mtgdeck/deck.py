"""Decklist parsing, card role heuristics, statistics and export formats."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

BASIC_LANDS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest", "Snow-Covered Wastes",
}  # fmt: skip

_LINE_RE = re.compile(r"^\s*(?:(\d+)\s*x?\s+)?(.+?)\s*$", re.IGNORECASE)
# Trailing set/collector info as exported by Moxfield/Archidekt: "Sol Ring (C21) 263 *F*"
_SET_SUFFIX_RE = re.compile(r"\s+\([A-Za-z0-9]{2,6}\)(?:\s+[\w-]+)?(?:\s+\*[A-Z]+\*)*\s*$")
_SECTION_RE = re.compile(r"^\s*(//|#)?\s*(commanders?|deck|main(board)?|sideboard|maybeboard|companion)\s*:?\s*$", re.I)


@dataclass
class DeckEntry:
    name: str
    qty: int = 1


@dataclass
class ParsedList:
    commanders: list[str] = field(default_factory=list)
    entries: list[DeckEntry] = field(default_factory=list)


def parse_decklist(text_or_lines: str | list[str]) -> ParsedList:
    """Parse a plain text decklist ('1 Sol Ring', '1x Sol Ring', 'Sol Ring (C21) 263').

    A section header 'Commander' / '// Commander' marks commander lines. Sideboard/maybeboard
    sections are ignored.
    """
    lines = text_or_lines.splitlines() if isinstance(text_or_lines, str) else list(text_or_lines)
    out = ParsedList()
    section = "deck"
    counts: dict[str, int] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        sec = _SECTION_RE.match(line)
        if sec:
            word = sec.group(2).lower()
            section = "commander" if word.startswith("commander") else ("skip" if word in {"sideboard", "maybeboard", "companion"} else "deck")
            continue
        if line.startswith(("#", "//")):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        qty = int(m.group(1) or 1)
        name = _SET_SUFFIX_RE.sub("", m.group(2)).strip()
        if name.endswith("*CMDR*"):
            name, section_for_line = name[: -len("*CMDR*")].strip(), "commander"
        else:
            section_for_line = section
        if section_for_line == "skip":
            continue
        if section_for_line == "commander":
            out.commanders.append(name)
        else:
            counts[name] = counts.get(name, 0) + qty
    out.entries = [DeckEntry(n, q) for n, q in counts.items()]
    return out


# --- role heuristics -------------------------------------------------------------------------
# Rough regexes over oracle text. Good enough for "does this deck have ~10 ramp pieces?",
# not a rules engine.
_ROLE_PATTERNS: dict[str, list[str]] = {
    "ramp": [
        r"add \{[wubrgc]\}", r"add (one|two|three) mana", r"add .* mana of any",
        r"search your library for (a|an|up to \w+) .*(land|forest|plains|island|swamp|mountain)",
        r"put (a|up to \w+) land cards? from your hand onto the battlefield",
        r"you may play (an|two) additional lands?",
    ],
    "card_draw": [
        r"draw (a|two|three|four|x|\w+) cards?", r"draws? cards? equal", r"exile the top .* you may (play|cast)",
        r"investigate",
    ],
    "removal": [
        r"destroy target", r"exile target", r"return target (nonland )?(creature|permanent|artifact|enchantment).* to (its|their) owner'?s? hand",
        r"deals? \w+ damage to (any target|target creature|target planeswalker)", r"counter target",
        r"target (creature|player|opponent) sacrifices", r"fights? (another )?target",
    ],
    "board_wipe": [
        r"destroy all", r"exile all", r"deals? \w+ damage to each creature", r"all creatures get -",
        r"return all (nonland )?(creatures|permanents)", r"each (player|opponent) sacrifices (all|\w+ creatures)",
    ],
    "tutor": [r"search your library for (a|an|up to \w+) (?!(basic )?(land|forest|plains|island|swamp|mountain))[^.]*card"],
    "extra_turn": [r"takes? (an|\w+) extra turns?"],
    "counterspell": [r"counter target (spell|noncreature spell|instant|creature spell)"],
    "protection": [r"hexproof", r"indestructible", r"phase out", r"protection from"],
}
_ROLE_RE = {role: [re.compile(p, re.I) for p in pats] for role, pats in _ROLE_PATTERNS.items()}


# Scryfall Tagger oracle tags (https://tagger.scryfall.com) per role. Preferred over the regexes
# whenever the local card DB has tag data for a card.
ROLE_TAGS: dict[str, set[str]] = {
    "ramp": {"ramp"},
    "card_draw": {"draw", "card-advantage"},
    "removal": {"removal"},
    "board_wipe": {"sweeper", "boardwipe"},
    "tutor": {"tutor"},
    "extra_turn": {"extra-turn"},
    "counterspell": {"counterspell"},
}


def card_roles(card: dict[str, Any]) -> list[str]:
    text = card.get("oracle_text") or ""
    type_line = card.get("type_line") or ""
    tags = set(card.get("tags") or [])
    if tags:
        roles = [role for role, wanted in ROLE_TAGS.items() if tags & wanted]
        roles += [r for r in _ROLE_RE if r not in ROLE_TAGS and any(p.search(text) for p in _ROLE_RE[r])]
    else:
        roles = [role for role, pats in _ROLE_RE.items() if any(p.search(text) for p in pats)]
    if "Land" in type_line.split("//")[0]:
        # Lands that tap for mana are not "ramp"; fetch/tutor wording on lands is not a tutor.
        roles = [r for r in roles if r not in {"ramp", "tutor"}]
    return sorted(set(roles))


def primary_type(type_line: str) -> str:
    front = (type_line or "").split("//")[0]
    for t in ("Land", "Creature", "Planeswalker", "Battle", "Artifact", "Enchantment", "Instant", "Sorcery"):
        if t in front:
            return t
    return "Other"


def deck_stats(cards: list[dict[str, Any]], qty: dict[str, int], currency: str = "eur") -> dict[str, Any]:
    """Statistics over compacted cards (see scryfall.compact). qty maps name -> count."""
    curve: Counter[int] = Counter()
    types: Counter[str] = Counter()
    roles: dict[str, list[str]] = {}
    pips: Counter[str] = Counter()
    total_price = 0.0
    missing_price: list[str] = []
    nonland_cmc: list[float] = []

    for c in cards:
        n = qty.get(c["name"], 1)
        ptype = primary_type(c.get("type_line", ""))
        types[ptype] += n
        if ptype != "Land":
            cmc = int(c.get("cmc") or 0)
            curve[min(cmc, 7)] += n
            nonland_cmc.extend([c.get("cmc") or 0] * n)
        for sym in re.findall(r"\{([WUBRG])(?:/[WUBRGP])?\}", c.get("mana_cost") or ""):
            pips[sym] += n
        for r in card_roles(c):
            roles.setdefault(r, []).append(c["name"])
        price = c.get(f"price_{currency}")
        if price:
            total_price += float(price) * n
        elif c["name"] not in BASIC_LANDS:
            missing_price.append(c["name"])

    return {
        "card_count": sum(qty.get(c["name"], 1) for c in cards),
        "types": dict(types.most_common()),
        "mana_curve": {("7+" if k == 7 else str(k)): curve[k] for k in sorted(curve)},
        "avg_cmc_nonland": round(sum(nonland_cmc) / len(nonland_cmc), 2) if nonland_cmc else 0,
        "color_pips": dict(pips.most_common()),
        "role_counts": {r: len(v) for r, v in sorted(roles.items())},
        "roles": roles,
        f"total_price_{currency}": round(total_price, 2),
        "cards_without_price": missing_price,
    }


def to_text(commanders: list[str], entries: list[DeckEntry]) -> str:
    """Plain text export, importable in Moxfield, Archidekt, ManaBox, MTGO-style tools."""
    lines = [f"1 {c} *CMDR*" for c in commanders]
    lines += [f"{e.qty} {e.name}" for e in sorted(entries, key=lambda e: e.name)]
    return "\n".join(lines) + "\n"


def to_sectioned_text(commanders: list[str], entries: list[DeckEntry]) -> str:
    lines = ["Commander"] + [f"1 {c}" for c in commanders] + ["", "Deck"]
    lines += [f"{e.qty} {e.name}" for e in sorted(entries, key=lambda e: e.name)]
    return "\n".join(lines) + "\n"
