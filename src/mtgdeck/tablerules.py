"""Table rules ("Tischregeln"): named rule sets for a playgroup ("Freitagsrunde", "Laden-Abend").

Unlike the blacklist, which is personal and always applies, a deck *chooses* a table rule (or none);
the deck stores its id as ``deck["table_rule"]`` and every validation checks it. A rule set holds

- ``rules``: the same rule lines as the blacklist (``@true-duals``, ``@cheap-tutors``, ``@price>20``,
  ``@two-card-combos``, ``@text:…`` = free-text agreement, not checked automatically);
- ``cards``: single banned cards (Oracle names);
- limits for the whole deck: ``max_bracket``, ``max_game_changers``, ``max_tutors``, ``deck_budget``
  (in ``currency``, counted for proxy decks too – it is about fairness, not money) and ``no_proxies``.

Stored in ``tablerules.json`` (MTG_TABLERULES_FILE, default <repo>/tablerules.json, gitignored).
Power-profile house rules of a deck stay as they are; both are checked, so the stricter one wins.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import blacklist
from .jsonstore import locked, read_json, write_json
from . import paths

TABLERULES_FILE = Path(os.environ.get("MTG_TABLERULES_FILE", paths.home() / "tablerules.json"))
LIMITS = ("max_bracket", "max_game_changers", "max_tutors", "deck_budget")
_CUR = {"eur": "€", "usd": "$"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _read() -> list[dict[str, Any]]:
    data = read_json(TABLERULES_FILE, {})
    return [s for s in data.get("sets", []) if isinstance(s, dict) and s.get("id")] if isinstance(data, dict) else []


def _write(sets: list[dict[str, Any]]) -> None:
    write_json(TABLERULES_FILE, {"sets": sets})


def get(rule_id: str | None) -> dict[str, Any] | None:
    if not rule_id:
        return None
    return next((s for s in _read() if s["id"] == rule_id), None)


def describe(rs: dict[str, Any]) -> dict[str, Any]:
    """The rule set plus described rules and plain-language summary lines."""
    return {**rs, "rule_info": blacklist.describe_rules(rs.get("rules", [])), "summary": summary_lines(rs)}


def all_sets() -> list[dict[str, Any]]:
    return [describe(s) for s in sorted(_read(), key=lambda s: s.get("name", "").lower())]


def _money(value: float, currency: str) -> str:
    return f"{value:g}".replace(".", ",") + f" {_CUR.get(currency, currency.upper())}"


def summary_lines(rs: dict[str, Any]) -> list[str]:
    """German one-liners of everything the rule set demands."""
    out = []
    if rs.get("max_bracket"):
        out.append(f"Höchstens Bracket {rs['max_bracket']}")
    if rs.get("max_game_changers") is not None:
        n = rs["max_game_changers"]
        out.append("Keine Game Changer" if n == 0 else f"Höchstens {n} Game Changer")
    if rs.get("max_tutors") is not None:
        n = rs["max_tutors"]
        out.append("Keine Tutoren" if n == 0 else f"Höchstens {n} Tutor{'' if n == 1 else 'en'}")
    if rs.get("deck_budget"):
        out.append(f"Deckbudget {_money(rs['deck_budget'], rs.get('currency', 'eur'))} (zählt auch für Proxy-Decks)")
    if rs.get("no_proxies"):
        out.append("Keine Proxies")
    info = blacklist.describe_rules(rs.get("rules", []))
    checked = [r["label"] for r in info if r["checked"]]
    if checked:
        out.append("Verboten: " + ", ".join(checked))
    if rs.get("cards"):
        out.append("Verbotene Karten: " + ", ".join(rs["cards"]))
    free = [r["label"] for r in info if not r["checked"]]
    if free:
        out.append("Absprachen: " + "; ".join(free))
    return out


def _clean_limits(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in LIMITS:
        if key not in changes:
            continue
        v = changes[key]
        if v in (None, ""):
            out[key] = None
            continue
        v = float(v) if key == "deck_budget" else int(v)
        if v < 0 or (key == "max_bracket" and not 1 <= v <= 5):
            raise ValueError(f"Ungültiger Wert für {key}: {v}")
        out[key] = None if key == "deck_budget" and v == 0 else v
    if "no_proxies" in changes:
        out["no_proxies"] = bool(changes["no_proxies"])
    if "currency" in changes and changes["currency"] in _CUR:
        out["currency"] = changes["currency"]
    return out


async def create(name: str, **changes: Any) -> dict[str, Any]:
    name = name.strip()[:80]
    if not name:
        raise ValueError("Bitte einen Namen angeben, z. B. „Freitagsrunde“.")
    rs = {"id": uuid.uuid4().hex[:8], "name": name, "description": "", "rules": [], "cards": [],
          "max_bracket": None, "max_game_changers": None, "max_tutors": None, "deck_budget": None,
          "currency": "eur", "no_proxies": False, "created": _now()}  # fmt: skip
    with locked(TABLERULES_FILE):
        _write([*_read(), rs])
    return await update(rs["id"], **changes) if changes else {**describe(get(rs["id"]) or rs), "not_found": []}


async def update(rule_id: str, *, add: list[str] | None = None, remove: list[str] | None = None, **changes: Any) -> dict[str, Any]:
    """Change a rule set: ``name``, ``description``, limits (None clears), ``add`` (cards or terms like
    "True Duals", "teurer als 20 €", "@freie Absprache"), ``remove`` (card names or rule lines)."""
    if get(rule_id) is None:
        raise FileNotFoundError(f"Unbekannte Tischregel: {rule_id}")
    new_rules, new_cards, not_found = await blacklist.parse_entries(add or [])  # slow lookups before the lock
    with locked(TABLERULES_FILE):
        rs = _update_locked(rule_id, new_rules, new_cards, remove, changes)
    return {**describe(rs), "not_found": not_found, "added_rules": blacklist.describe_rules(new_rules), "added": new_cards}


def _update_locked(rule_id: str, new_rules: list[str], new_cards: list[str], remove: list[str] | None,
                   changes: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    sets = _read()
    rs = next((s for s in sets if s["id"] == rule_id), None)
    if rs is None:
        raise FileNotFoundError(f"Unbekannte Tischregel: {rule_id}")
    if "name" in changes and str(changes["name"] or "").strip():
        rs["name"] = str(changes["name"]).strip()[:80]
    if "description" in changes:
        rs["description"] = str(changes["description"] or "").strip()[:500]
    rs.update(_clean_limits(changes))
    rs["rules"] = blacklist.merge_rules(rs.get("rules", []), new_rules)
    cards = {c.lower(): c for c in rs.get("cards", [])}
    for c in new_cards:
        cards[c.lower()] = c
    for r in remove or []:
        r = r.strip()
        if r.startswith("@"):
            rs["rules"] = [x for x in rs["rules"] if x != r]
        else:
            cards.pop(r.lower(), None)
    rs["cards"] = sorted(cards.values(), key=str.lower)
    rs["updated"] = _now()
    _write(sets)
    return rs


def delete(rule_id: str) -> None:
    with locked(TABLERULES_FILE):
        sets = _read()
        if not any(s["id"] == rule_id for s in sets):
            raise FileNotFoundError(f"Unbekannte Tischregel: {rule_id}")
        _write([s for s in sets if s["id"] != rule_id])


def resolve_id(value: str | None) -> dict[str, Any] | None:
    """A rule set by id or (case-insensitive) name."""
    if not value:
        return None
    return get(value) or next((s for s in _read() if s.get("name", "").lower() == value.strip().lower()), None)


# --- checking ---------------------------------------------------------------------------------


def violations(
    rs: dict[str, Any],
    *,
    cards: list[dict[str, Any]],
    qty: dict[str, int],
    bracket: int,
    proxy: bool,
    game_changers: list[str],
    tutors: list[str],
    two_card_combos: list[dict[str, Any]],
    estimated: int | None = None,
) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a deck against a rule set. ``cards`` are compact cards incl. commanders,
    ``qty`` their counts (commanders 1)."""
    head = f"Tischregel „{rs['name']}“"
    errors: list[str] = []
    warnings: list[str] = []
    mb = rs.get("max_bracket")
    if mb and bracket > mb:
        errors.append(f"{head}: höchstens Bracket {mb} – das Deck ist für Bracket {bracket} gebaut")
    elif mb and estimated and estimated > mb:
        warnings.append(f"{head}: höchstens Bracket {mb} – die Schätzung sieht das Deck bei Bracket {estimated}")
    gc_max, tu_max = rs.get("max_game_changers"), rs.get("max_tutors")
    if gc_max is not None and len(game_changers) > gc_max:
        allowed = "keine Game Changer" if gc_max == 0 else f"höchstens {gc_max} Game Changer"
        errors.append(f"{head}: {allowed}, im Deck {len(game_changers)}: {', '.join(game_changers)}")
    if tu_max is not None and len(tutors) > tu_max:
        allowed = "keine Tutoren" if tu_max == 0 else f"höchstens {tu_max} Tutor{'' if tu_max == 1 else 'en'}"
        errors.append(f"{head}: {allowed}, im Deck {len(tutors)}: {', '.join(tutors)}")
    if rs.get("no_proxies") and proxy:
        errors.append(f"{head}: keine Proxies – dieses Deck ist als Proxy-Deck markiert")
    currency = rs.get("currency", "eur")
    if rs.get("deck_budget"):
        total = 0.0
        for c in cards:
            try:
                total += float(c.get(f"price_{currency}") or 0) * qty.get(c["name"], 1)
            except (TypeError, ValueError):
                pass
        if total > rs["deck_budget"]:
            errors.append(f"{head}: Deckbudget {_money(rs['deck_budget'], currency)}, das Deck kostet {_money(round(total, 2), currency)}")
    banned = {c.lower() for c in rs.get("cards", [])}
    hits = [c["name"] for c in cards if c["name"].lower() in banned]
    if hits:
        errors.append(f"{head}: verbotene Karten {', '.join(hits)}")
    info = blacklist.describe_rules(rs.get("rules", []))
    active = [r for r in info if r["checked"] and not r.get("deck_level")]
    by_rule: dict[str, list[str]] = {}
    for c in cards:
        if c["name"] in hits:
            continue
        for label in blacklist.card_rules(c, active=active, currency=currency):
            by_rule.setdefault(label, []).append(c["name"])
    for label, names in by_rule.items():
        errors.append(f"{head}: „{label}“ verboten – {', '.join(names)}")
    if any(r["key"] == "two-card-combos" for r in info) and two_card_combos:
        pairs = "; ".join(" + ".join(c.get("cards") or ["?"]) for c in two_card_combos[:5])
        errors.append(f"{head}: keine 2-Karten-Combos – {pairs}")
    return errors, warnings


def result(rs: dict[str, Any] | None, rule_id: str | None, errors: list[str], warnings: list[str]) -> dict[str, Any] | None:
    """The ``validation["table_rule"]`` block."""
    if not rule_id:
        return None
    if rs is None:
        return {"id": rule_id, "name": None, "missing": True, "compliant": True, "violations": [], "warnings": [], "notes": []}
    notes = [r["label"] for r in blacklist.describe_rules(rs.get("rules", [])) if not r["checked"]]
    return {"id": rs["id"], "name": rs["name"], "compliant": not errors, "violations": errors, "warnings": warnings,
            "notes": notes, "summary": summary_lines(rs)}  # fmt: skip


async def check_saved(rs: dict[str, Any], deck: dict[str, Any]) -> dict[str, Any]:
    """Check a saved deck against a rule set without re-validating it (card data from the local DB /
    cache, combos and bracket estimate from the stored validation)."""
    from .cards import resolve

    names = [*deck.get("commanders", []), *(c["name"] for c in deck.get("cards", []))]
    data, renames, _ = await resolve(names)
    qty = {renames.get(c["name"], c["name"]): int(c.get("qty", 1)) for c in deck.get("cards", [])}
    cards = [data[renames.get(n, n)] for n in dict.fromkeys(names) if renames.get(n, n) in data]
    stored = ((deck.get("validation") or {}).get("bracket")) or {}
    errors, warnings = violations(
        rs, cards=cards, qty=qty, bracket=int(deck.get("bracket") or 3), proxy=bool(deck.get("proxy")),
        game_changers=sorted({c["name"] for c in cards if c.get("game_changer")} | set(stored.get("game_changers") or [])),
        tutors=sorted(c["name"] for c in cards if "tutor" in (c.get("roles") or []) and "Land" not in (c.get("type_line") or "")),
        two_card_combos=stored.get("two_card_combos") or [], estimated=stored.get("estimated"),
    )  # fmt: skip
    return {"slug": deck["slug"], "name": deck.get("name") or deck["slug"], "commanders": deck.get("commanders", []),
            "bracket": deck.get("bracket"), "uses": deck.get("table_rule") == rs["id"], "ok": not errors,
            "errors": errors, "warnings": warnings}  # fmt: skip


async def check_all(rule_id: str) -> list[dict[str, Any]]:
    """Every saved deck against one rule set ("Welche Decks passen zu dieser Runde?")."""
    from . import storage

    rs = get(rule_id)
    if rs is None:
        raise FileNotFoundError(f"Unbekannte Tischregel: {rule_id}")
    out = []
    for d in storage.list_decks():
        try:
            out.append(await check_saved(rs, storage.load(d["slug"])))
        except (FileNotFoundError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda r: (not r["ok"], not r["uses"], r["name"].lower()))


async def revalidate_decks(rule_id: str) -> list[str]:
    """Re-validate the decks that use ``rule_id`` (after the rule set changed); no new version."""
    from . import deckedit, storage

    done = []
    for d in storage.list_decks():
        try:
            deck = storage.load(d["slug"])
        except FileNotFoundError:
            continue
        if deck.get("table_rule") != rule_id:
            continue
        try:
            await deckedit.revalidate(deck)
            storage.save(deck, expect_version=deck.get("version"))
        except storage.ConflictError:  # saved meanwhile (e.g. by a Claude job) – that save validated already
            continue
        except Exception:  # network trouble: keep the old validation
            continue
        done.append(deck["slug"])
    return done


def prompt_lines(rs: dict[str, Any] | None) -> list[str]:
    """Prompt lines for Claude: what the table demands and how to pass it on."""
    if not rs:
        return []
    lines = [f"- Tischregel „{rs['name']}“ gilt für dieses Deck" + (f" ({rs['description']})" if rs.get("description") else "") + ":"]
    lines += [f"  - {line}" for line in summary_lines(rs)]
    lines.append(f"  - Übergib `table_rule=\"{rs['id']}\"` an `validate_deck` und `save_deck`; Verstöße sind Fehler. "
                 "Freie Absprachen prüft niemand automatisch – halte sie selbst ein.")  # fmt: skip
    return lines
