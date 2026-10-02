"""User-maintained blacklist: cards – and whole groups of cards – that must never be put into a deck.

Stored as plain text so it can also be edited by hand. Location: MTG_BLACKLIST_FILE, default
<repo>/blacklist.txt (gitignored). One entry per line, '#' comments allowed:

- a card: its English Oracle name (``Sol Ring``);
- a rule: ``@<key>`` for a known term from ``RULES`` (``@true-duals``, ``@cheap-tutors``, ``@fast-mana`` …);
- a price limit: ``@price>20`` (cards more expensive than 20 in the deck's currency);
- a free-text term: ``@text:<anything>`` – not checked automatically, Claude reads and respects it.

Known terms are recognised in German and English (``True-Duals``, ``Günstige Tutoren``, ``Fetchlands``,
``teurer als 20 €`` …), see ``parse_rule()``. Rules are either curated name lists or predicates on the
compact card data (``scryfall.compact()`` plus ``tags``/``roles`` where present); ``two-card-combos`` is a
deck-level rule that ``validate_deck`` checks against Commander Spellbook.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from .cards import resolve
from .storage import PROJECT_ROOT

BLACKLIST_FILE = Path(os.environ.get("MTG_BLACKLIST_FILE", PROJECT_ROOT / "blacklist.txt"))

_HEADER = (
    "# Karten und Regeln, die nie in ein Deck dürfen. Eine pro Zeile:\n"
    "#   Kartenname (englischer Oracle-Name) · @regel (z. B. @true-duals, @cheap-tutors) · @price>20 · @text:Freitext\n"
)

TRUE_DUALS = {"Underground Sea", "Volcanic Island", "Tropical Island", "Badlands", "Bayou", "Plateau", "Savannah",
              "Scrubland", "Taiga", "Tundra"}  # fmt: skip
FETCHLANDS = {"Arid Mesa", "Bloodstained Mire", "Flooded Strand", "Marsh Flats", "Misty Rainforest", "Polluted Delta",
              "Scalding Tarn", "Verdant Catacombs", "Windswept Heath", "Wooded Foothills", "Prismatic Vista"}  # fmt: skip
SHOCKLANDS = {"Blood Crypt", "Breeding Pool", "Godless Shrine", "Hallowed Fountain", "Overgrown Tomb", "Sacred Foundry",
              "Steam Vents", "Stomping Ground", "Temple Garden", "Watery Grave"}  # fmt: skip
# Commonly named stax / hatebear pieces (curated; extend by adding single cards to the blacklist).
STAX = {
    "Winter Orb", "Static Orb", "Stasis", "Tangle Wire", "Smokestack", "Trinisphere", "Sphere of Resistance",
    "Thorn of Amethyst", "Thalia, Guardian of Thraben", "Vryn Wingmare", "Glowrider", "Lodestone Golem",
    "Rule of Law", "Deafening Silence", "Arcane Laboratory", "Eidolon of Rhetoric", "Archon of Emeria",
    "Drannith Magistrate", "Opposition Agent", "Collector Ouphe", "Null Rod", "Stony Silence", "Cursed Totem",
    "Linvala, Keeper of Silence", "Hushbringer", "Hushwing Gryff", "Torpor Orb", "Root Maze", "Kismet",
    "Aven Mindcensor", "Narset, Parter of Veils", "Notion Thief", "Ethersworn Canonist", "Blood Moon",
    "Magus of the Moon", "Back to Basics", "Hokori, Dust Drinker", "Rising Waters", "Armageddon Clock",
    "Grand Arbiter Augustin IV", "Thalia, Heretic Cathar", "Authority of the Consuls", "Blind Obedience",
    "Kataki, War's Wage", "Damping Sphere", "Pithing Needle", "Sorcerous Spyglass",
    "Teferi, Time Raveler", "Elesh Norn, Mother of Machines", "Hall of Gemstone", "Mana Vortex", "Tsabo's Decree",
    "Ruination", "Stranglehold", "Aura of Silence", "Nether Void", "Tabernacle at Pendrell Vale",
}  # fmt: skip


def _text(c: dict[str, Any]) -> str:
    return c.get("oracle_text") or ""


def _front_type(c: dict[str, Any]) -> str:
    return (c.get("type_line") or "").split("//")[0]


def _roles(c: dict[str, Any]) -> set[str]:
    if "roles" in c:
        return set(c.get("roles") or [])
    from .deck import card_roles

    return set(card_roles(c))


def _is_tutor(c: dict[str, Any]) -> bool:
    return "Land" not in _front_type(c) and "tutor" in _roles(c)


def _is_cheap_tutor(c: dict[str, Any]) -> bool:
    return _is_tutor(c) and float(c.get("cmc") or 0) <= 2


def _is_fast_mana(c: dict[str, Any]) -> bool:
    from .power import _is_fast_mana as fast

    return fast(c)


def _is_free_spell(c: dict[str, Any]) -> bool:
    from .power import _FREE_SPELL_RE

    return bool(_FREE_SPELL_RE.search(_text(c)))


def _is_extra_turn(c: dict[str, Any]) -> bool:
    from .brackets import _EXTRA_TURN_RE

    return bool(_EXTRA_TURN_RE.search(_text(c)))


def _is_mld(c: dict[str, Any]) -> bool:
    from .brackets import _MLD_RE, MASS_LAND_DENIAL

    return c.get("name") in MASS_LAND_DENIAL or bool(_MLD_RE.search(_text(c)))


def _role(role: str) -> Callable[[dict[str, Any]], bool]:
    return lambda c: role in _roles(c)


# key -> label, description, aliases (normalised, see _norm), names (curated list) and/or test (predicate).
# ``deck`` rules are checked on the whole deck (validate_deck), not per card.
RULES: dict[str, dict[str, Any]] = {
    "true-duals": {"label": "True Duals", "description": "Die zehn originalen Dual Lands (Underground Sea, Volcanic Island …).",
                   "aliases": ["trueduals", "truedual", "duals", "duallands", "originaleduals", "abur", "aburduals", "originaldual"],
                   "names": TRUE_DUALS},
    "fetchlands": {"label": "Fetchlands", "description": "Die zehn Fetchlands plus Prismatic Vista.",
                   "aliases": ["fetchlands", "fetchland", "fetches", "fetch"], "names": FETCHLANDS},
    "shocklands": {"label": "Shocklands", "description": "Die zehn Shocklands (Watery Grave, Steam Vents …).",
                   "aliases": ["shocklands", "shockland", "shocks"], "names": SHOCKLANDS},
    "cheap-tutors": {"label": "Günstige Tutoren", "description": "Tutoren mit Manawert 2 oder weniger (Demonic, Vampiric, Enlightened Tutor …).",
                     "aliases": ["guenstigetutoren", "guenstigetutor", "billigetutoren", "cheaptutors", "cheaptutor", "effizientetutoren",
                                 "schnelletutoren", "guenstigetutors"], "test": _is_cheap_tutor},
    "tutors": {"label": "Alle Tutoren", "description": "Alles, was eine beliebige Nicht-Land-Karte aus der Bibliothek sucht.",
               "aliases": ["tutoren", "tutor", "tutors", "alletutoren", "alletutors", "suchkarten"], "test": _is_tutor},
    "fast-mana": {"label": "Fast Mana", "description": "Beschleuniger wie Sol Ring, Mana Crypt, Mox, Ancient Tomb (0–1 Mana bzw. 2 Mana für 2+).",
                  "aliases": ["fastmana", "schnellesmana", "moxe", "moxen", "manabeschleuniger"], "test": _is_fast_mana},
    "game-changers": {"label": "Game Changer", "description": "Alle Karten der offiziellen Game-Changer-Liste.",
                      "aliases": ["gamechanger", "gamechangers", "gc"], "test": lambda c: bool(c.get("game_changer"))},
    "extra-turns": {"label": "Extra-Züge", "description": "Karten, die zusätzliche Züge geben.",
                    "aliases": ["extraturns", "extraturn", "extrazuege", "extrazug", "zusaetzlichezuege", "zusaetzlicherzug"],
                    "test": _is_extra_turn},
    "mld": {"label": "Massen-Landzerstörung", "description": "Armageddon & Co. – zerstört oder sperrt viele Länder.",
            "aliases": ["mld", "masslanddenial", "masslanddestruction", "landzerstoerung", "massenlandzerstoerung",
                        "masslandzerstoerung", "landdestruction"], "test": _is_mld},
    "free-spells": {"label": "Gratis-Zauber", "description": "Zauber ohne Manakosten (Force of Will, Fierce Guardianship …).",
                    "aliases": ["freespells", "freespell", "freeinteraction", "gratiszauber", "kostenlosezauber", "freiezauber"],
                    "test": _is_free_spell},
    "counterspells": {"label": "Counterspells", "description": "Konterzauber aller Art.",
                      "aliases": ["counterspells", "counterspell", "counter", "counters", "konterzauber", "neutralisieren"],
                      "test": _role("counterspell")},
    "board-wipes": {"label": "Board Wipes", "description": "Massenvernichtung (Wrath of God, Cyclonic Rift …).",
                    "aliases": ["boardwipes", "boardwipe", "wipes", "wraths", "sweeper", "sweepers", "massenvernichtung",
                                "massenentfernung"], "test": _role("board_wipe")},
    "stax": {"label": "Stax", "description": "Bekannte Stax- und Hatebear-Karten (kuratierte Liste: Winter Orb, Rule of Law, Drannith Magistrate …).",
             "aliases": ["stax", "staxkarten", "hatebears", "hatebear", "lock", "locks", "prison"], "names": STAX},
    "two-card-combos": {"label": "2-Karten-Combos", "description": "Keine zwei Karten im Deck, die zusammen sofort gewinnen oder eine Endlosschleife bilden (Commander Spellbook).",
                        "aliases": ["2kartencombos", "zweikartencombos", "2kartenkombos", "zweikartenkombos", "twocardcombos",
                                    "2cardcombos", "combos", "kombos", "infinitecombos", "endloscombos", "infinites"], "deck": True},
}  # fmt: skip

_ALIASES = {a: key for key, r in RULES.items() for a in [*r["aliases"], key.replace("-", "")]}
_PRICE_RE = re.compile(
    r"^(?:@?price>|preis>|>|teurer als|ueber|über|mehr als|ab|karten ueber|karten über|alles ueber|alles über)\s*"
    r"(\d+(?:[.,]\d+)?)\s*(€|eur|euro|\$|usd|dollar)?\s*(?:teure karten)?$", re.I)  # fmt: skip


def _norm(text: str) -> str:
    t = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]", "", t)


def parse_rule(text: str) -> str | None:
    """A typed term -> its stored rule line (``@cheap-tutors``, ``@price>20``), or None if it is no known term.
    Lines starting with ``@`` that match nothing known become free-text rules (``@text:…``)."""
    raw = text.strip()
    forced = raw.startswith("@")
    body = raw[1:].strip() if forced else raw
    if body.lower().startswith("text:"):
        free = body[5:].strip()
        return f"@text:{free}" if free else None
    m = _PRICE_RE.match(body.replace(" ", " "))
    if m:
        amount = float(m.group(1).replace(",", "."))
        cur = "usd" if (m.group(2) or "").lower() in ("$", "usd", "dollar") else ""
        return f"@price>{amount:g}{cur}" if amount > 0 else None
    key = _ALIASES.get(_norm(body))
    if key:
        return f"@{key}"
    return f"@text:{body}" if forced and body else None


def _describe(line: str) -> dict[str, Any]:
    """Stored rule line -> {rule, key, label, description, checked}."""
    body = line[1:]
    if body.startswith("text:"):
        return {"rule": line, "key": "text", "label": body[5:], "checked": False,
                "description": "Freier Begriff – wird nicht automatisch geprüft, Claude beachtet ihn beim Bauen."}  # fmt: skip
    m = re.fullmatch(r"price>(\d+(?:\.\d+)?)(usd)?", body)
    if m:
        cur = "$" if m.group(2) else "€"
        amount = m.group(1).replace(".", ",")
        return {"rule": line, "key": "price", "label": f"Teurer als {amount} {cur}", "checked": True,
                "limit": float(m.group(1)), "currency": "usd" if m.group(2) else None,
                "description": f"Karten, deren günstigster Druck mehr als {amount} {cur} kostet."}  # fmt: skip
    r = RULES.get(body)
    if r is None:
        return {"rule": line, "key": "text", "label": body, "checked": False, "description": "Unbekannte Regel – nur als Hinweis."}
    out = {"rule": line, "key": body, "label": r["label"], "description": r["description"], "checked": True}
    if r.get("deck"):
        out["deck_level"] = True
    if r.get("names"):
        out["cards"] = sorted(r["names"])
    return out


def _lines() -> list[str]:
    try:
        lines = BLACKLIST_FILE.read_text("utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def load() -> list[str]:
    """The blacklisted card names (sorted)."""
    return sorted({ln for ln in _lines() if not ln.startswith("@")}, key=str.lower)


def rule_lines() -> list[str]:
    return list(dict.fromkeys(ln for ln in _lines() if ln.startswith("@")))


def rules() -> list[dict[str, Any]]:
    """The active rules with label and description."""
    return [_describe(ln) for ln in rule_lines()]


def names_lower() -> set[str]:
    return {n.lower() for n in load()}


def catalog() -> list[dict[str, Any]]:
    """All known terms for quick-add buttons."""
    return [{"rule": f"@{k}", "label": r["label"], "description": r["description"]} for k, r in RULES.items()]


def _write(names: list[str], rule_list: list[str]) -> None:
    BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join([*sorted(set(names), key=str.lower), *dict.fromkeys(rule_list)])
    BLACKLIST_FILE.write_text(_HEADER + body + ("\n" if body else ""), "utf-8")


async def parse_entries(entries: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Typed entries -> (rule lines, Oracle card names, not found). Known terms ("True Duals",
    "Günstige Tutoren", "teurer als 20 €") become rules, ``@…`` always does (unknown = free text),
    everything else is resolved as a card name (German etc. works)."""
    rule_list: list[str] = []
    wanted: list[str] = []
    for a in entries:
        a = a.strip()
        if not a:
            continue
        rule = parse_rule(a)
        if rule:
            rule_list.append(rule)
        else:
            wanted.append(a)
    names: list[str] = []
    not_found: list[str] = []
    if wanted:
        try:
            cards, renames, not_found = await resolve(wanted)
        except Exception:  # offline: keep the names as typed
            return rule_list, list(wanted), []
        names = [renames.get(w, w) for w in wanted if w not in not_found and renames.get(w, w) in cards]
    return rule_list, names, not_found


def merge_rules(current: list[str], new: list[str]) -> list[str]:
    """Add rule lines; a new price limit replaces the old one."""
    out = list(current)
    for rule in new:
        if rule in out:
            continue
        if rule.startswith("@price>"):
            out = [r for r in out if not r.startswith("@price>")]
        out.append(rule)
    return out


def describe_rules(lines: list[str]) -> list[dict[str, Any]]:
    return [_describe(ln) for ln in dict.fromkeys(lines)]


async def update(add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
    """Add/remove cards and rules (see ``parse_entries``)."""
    current = {n.lower(): n for n in load()}
    added_rules, added, not_found = await parse_entries(add or [])
    current_rules = merge_rules(rule_lines(), added_rules)
    for name in added:
        current[name.lower()] = name
    removed = []
    for name in remove or []:
        name = name.strip()
        if name.startswith("@"):
            if name in current_rules:
                current_rules.remove(name)
                removed.append(name)
        elif current.pop(name.lower(), None) is not None:
            removed.append(name)
    _write(list(current.values()), current_rules)
    return {"blacklist": load(), "rules": rules(), "added": added, "added_rules": describe_rules(added_rules),
            "removed": removed, "not_found": not_found}  # fmt: skip


# --- checking cards ---------------------------------------------------------------------------


def _price(card: dict[str, Any], currency: str) -> float | None:
    for cur in (currency, "eur" if currency == "usd" else "usd"):
        try:
            v = card.get(f"price_{cur}")
            if v not in (None, ""):
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _has_data(card: dict[str, Any]) -> bool:
    return "type_line" in card or "oracle_text" in card


def card_rules(card: dict[str, Any], *, active: list[dict[str, Any]] | None = None, currency: str = "eur") -> list[str]:
    """Labels of the active per-card rules this card breaks (name lists always; predicates and the
    price limit only when the card carries its data)."""
    hits = []
    name = card.get("name") or ""
    for r in rules() if active is None else active:
        key = r["key"]
        if key == "price":
            if _has_data(card):
                p = _price(card, r.get("currency") or currency)
                if p is not None and p > r["limit"]:
                    hits.append(r["label"])
            continue
        spec = RULES.get(key)
        if not spec or spec.get("deck"):
            continue
        if spec.get("names") and name in spec["names"]:
            hits.append(r["label"])
        elif spec.get("test") and _has_data(card) and spec["test"](card):
            hits.append(r["label"])
    return hits


def deck_rule(key: str) -> dict[str, Any] | None:
    """The active deck-level rule ``key`` (e.g. ``two-card-combos``), if any."""
    return next((r for r in rules() if r["key"] == key), None)


def blocked(card: dict[str, Any], *, currency: str = "eur") -> bool:
    return (card.get("name") or "").lower() in names_lower() or bool(card_rules(card, currency=currency))


def filter_cards(cards: list[dict[str, Any]], key: str = "name", *, currency: str = "eur") -> list[dict[str, Any]]:
    """Drop blacklisted cards and cards that break a rule. Name-only entries (EDHREC) are looked up in
    the local card DB so predicate rules apply to them too, when it is available."""
    banned = names_lower()
    active = [r for r in rules() if r["checked"] and not r.get("deck_level")]
    if not banned and not active:
        return cards
    data: dict[str, dict[str, Any]] = {}
    needs_data = any(r["key"] == "price" or RULES.get(r["key"], {}).get("test") for r in active)
    if needs_data and any(not _has_data(c) for c in cards):
        from . import carddb

        if carddb.available():
            found, _ = carddb.lookup([c.get(key) or "" for c in cards if not _has_data(c)])
            data = {f.get("_query_name") or f["name"]: f for f in found}
    out = []
    for c in cards:
        name = c.get(key) or ""
        if name.lower() in banned:
            continue
        probe = c if _has_data(c) else {**data.get(name, {}), "name": name}
        if active and card_rules(probe, active=active, currency=currency):
            continue
        out.append(c)
    return out
