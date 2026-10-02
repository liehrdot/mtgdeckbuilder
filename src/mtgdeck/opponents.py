"""Opponent decks ("Gegnerdecks"): decks the user played against – only the commander and what stood out,
no card lists. Stored in ``decks/.opponents.json``::

    {"opponents": [{"id", "commanders": [...], "label", "player", "bracket", "table_rule", "tags": [...],
                    "notes": [{"id", "text", "at", "game_id", "deck_slug"}], "color_identity", "image",
                    "created", "updated"}]}

Games (``games.py``) link to them via ``opponent_ids``; a game against an unknown commander creates one.
``prompt_lines()`` tells Claude about the opponents that matter for a deck (faced with it, or playing at
its table rule) so rebuilds and upgrade suggestions can prepare for them; typical cards of an opponent's
commander come from EDHREC, since the user does not know the exact list.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from . import games, storage

FILE_NAME = ".opponents.json"
MAX_NOTES = 200
# quick-pick traits: key -> (label, what a deck needs against it)
TAGS: dict[str, tuple[str, str]] = {
    "combo": ("Combo", "Interaktion gegen Combos: Instant-Removal, Counter oder Friedhofs-Hass je nach Combo"),
    "fast": ("schnell & aggressiv", "früh blocken, günstige Interaktion, Lebenspunkte schützen"),
    "stax": ("Stax / Lock", "Artefakt- und Verzauberungs-Removal, nicht zu abhängig von einer Ressource"),
    "wipes": ("viele Board Wipes", "nicht alles auf den Tisch legen, Schutz oder schnelle Erholung"),
    "counters": ("viele Counterspells", "günstige Sprüche, Schutz gegen Counter, mehrere Bedrohungen pro Zug"),
    "removal": ("viel Removal", "Schutz für Commander und Schlüsselkarten, Redundanz"),
    "fliers": ("Flieger", "Antworten auf Flieger: Reichweite, Flieger, Removal"),
    "tokens": ("Token-Schwärme", "Board Wipes oder Massen-Removal für kleine Kreaturen"),
    "graveyard": ("Friedhof / Reanimation", "Friedhofs-Hass"),
    "lifegain": ("Lifegain", "Siegbedingungen, die nicht nur über Schaden laufen"),
    "voltron": ("Voltron / Commander-Schaden", "Removal und Blocker für einen großen Angreifer"),
    "artifacts": ("Artefakte", "Artefakt-Removal"),
    "spells": ("Spellslinger", "Counter, Druck auf frühe Engines"),
    "theft": ("Diebstahl / Kontrolle", "Schutz und Antworten auf eigene gestohlene Karten"),
    "lands": ("Landzerstörung", "genug Länder und Ramp, robuste Manabasis"),
    "politics": ("Politik / Gruppenspiel", "eigene Bedrohung klein wirken lassen, flexible Antworten"),
}  # fmt: skip


def _file() -> Path:
    return storage.DECKS_DIR / FILE_NAME


def _read() -> list[dict[str, Any]]:
    try:
        data = json.loads(_file().read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    items = data.get("opponents") if isinstance(data, dict) else None
    return [o for o in items or [] if isinstance(o, dict) and o.get("id")]


def _write(items: list[dict[str, Any]]) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps({"opponents": items}, ensure_ascii=False, indent=2), "utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def title(o: dict[str, Any]) -> str:
    """'Tims Atraxa (Atraxa, Praetors' Voice)' or just the commander(s)."""
    cmd = " + ".join(o.get("commanders") or []) or "?"
    return f"{o['label']} ({cmd})" if o.get("label") else cmd


def get(opp_id: str) -> dict[str, Any] | None:
    return next((o for o in _read() if o["id"] == opp_id), None)


def find(commander: str) -> list[dict[str, Any]]:
    """Opponent decks led by this commander (case-insensitive)."""
    c = commander.strip().lower()
    return [o for o in _read() if c in {n.lower() for n in o.get("commanders") or []}]


async def _card_info(names: list[str]) -> tuple[list[str], dict[str, Any], list[str]]:
    """Resolved Oracle names, data of the first commander, names not found."""
    from .cards import resolve

    try:
        data, renames, missing = await resolve(names)
    except Exception:  # offline: keep the names as typed
        return names, {}, []
    resolved = [renames.get(n, n) for n in names if n not in missing]
    return resolved, data.get(resolved[0], {}) if resolved else {}, missing


def _clean(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("label", "player"):
        if key in changes:
            out[key] = str(changes[key] or "").strip()[:80]
    if "bracket" in changes:
        b = changes["bracket"]
        out["bracket"] = int(b) if b not in (None, "") and 1 <= int(b) <= 5 else None
    if "table_rule" in changes:
        out["table_rule"] = changes["table_rule"] or None
    if "tags" in changes:
        out["tags"] = [t for t in dict.fromkeys(changes["tags"] or []) if t in TAGS]
    return out


async def create(commanders: list[str], *, note: str = "", **changes: Any) -> dict[str, Any]:
    names = [c.strip() for c in commanders if c and c.strip()][:2]
    if not names:
        raise ValueError("Bitte den Commander des Gegners angeben.")
    o = await _new(names, changes)
    if note.strip():
        o["notes"].append(_note(note))
    _write([*_read(), o])
    return o


async def _new(names: list[str], changes: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved, card, missing = await _card_info(names)
    if missing:
        raise ValueError(f"Commander nicht gefunden: {', '.join(missing)}")
    return {"id": uuid.uuid4().hex[:8], "commanders": resolved, "label": "", "player": "", "bracket": None, "table_rule": None,
            "tags": [], "notes": [], "color_identity": card.get("color_identity") or [], "image": card.get("image"),
            "created": _now(), "updated": _now(), **_clean(changes or {})}  # fmt: skip


def _note(text: str, game_id: str | None = None, deck_slug: str | None = None) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex[:8], "text": text.strip()[:1000], "at": _now(), "game_id": game_id, "deck_slug": deck_slug}


async def update(opp_id: str, *, commanders: list[str] | None = None, add_note: str = "", remove_note: str | None = None,
                 **changes: Any) -> dict[str, Any]:  # fmt: skip
    items = _read()
    o = next((x for x in items if x["id"] == opp_id), None)
    if o is None:
        raise FileNotFoundError(f"Unbekanntes Gegnerdeck: {opp_id}")
    if commanders:
        resolved, card, missing = await _card_info([c for c in commanders if c.strip()][:2])
        if missing:
            raise ValueError(f"Commander nicht gefunden: {', '.join(missing)}")
        o.update(commanders=resolved, color_identity=card.get("color_identity") or o.get("color_identity") or [],
                 image=card.get("image") or o.get("image"))  # fmt: skip
    o.update(_clean(changes))
    if add_note.strip():
        o["notes"] = (o.get("notes") or []) + [_note(add_note)]
        o["notes"] = o["notes"][-MAX_NOTES:]
    if remove_note:
        o["notes"] = [n for n in o.get("notes") or [] if n.get("id") != remove_note]
    o["updated"] = _now()
    _write(items)
    return o


def delete(opp_id: str) -> None:
    items = _read()
    if not any(o["id"] == opp_id for o in items):
        raise FileNotFoundError(f"Unbekanntes Gegnerdeck: {opp_id}")
    _write([o for o in items if o["id"] != opp_id])


async def link_game(deck_slug: str, game_id: str, slots: list[dict[str, Any]], *, remember: bool = True) -> list[str | None]:
    """Link the opponents of a logged game: ``slots`` = [{"commander", "id"?, "note"?}] in game order.
    Known decks are matched by id or (single) commander match; unknown commanders become new opponent
    decks when ``remember``; a note is stored as an observation. Returns the ids per slot."""
    ids: list[str | None] = []
    items = _read()
    changed = False
    for slot in slots:
        name = (slot.get("commander") or "").strip()
        o = next((x for x in items if x["id"] == slot.get("id")), None) if slot.get("id") else None
        if o is None and name:
            matches = [x for x in items if name.lower() in {n.lower() for n in x.get("commanders") or []}]
            o = max(matches, key=lambda x: x.get("updated") or "") if matches else None
        if o is None and name and remember:
            try:
                o = await _new([name])
                items.append(o)
            except ValueError:
                o = None
        if o is not None:
            if (slot.get("note") or "").strip():
                o["notes"] = ((o.get("notes") or []) + [_note(slot["note"], game_id, deck_slug)])[-MAX_NOTES:]
            o["updated"] = _now()
            changed = True
        ids.append(o["id"] if o else None)
    if changed:
        _write(items)
    return ids


def _all_games() -> list[tuple[str, str, dict[str, Any]]]:
    """(deck slug, deck name, game) over every saved deck."""
    out = []
    for d in storage.list_decks():
        for g in games.games(d["slug"]):
            out.append((d["slug"], d["name"], g))
    return out


def _faced(o: dict[str, Any], g: dict[str, Any]) -> bool:
    if o["id"] in (g.get("opponent_ids") or []):
        return True
    names = {n.lower() for n in o.get("commanders") or []}
    linked = [i for i in g.get("opponent_ids") or [] if i]
    return not linked and any(n.lower() in names for n in g.get("opponents") or [])


def record(o: dict[str, Any], all_games: list[tuple[str, str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Games against this opponent over all decks: record, per deck, the games themselves."""
    rows = [(slug, name, g) for slug, name, g in (all_games if all_games is not None else _all_games()) if _faced(o, g)]
    results = Counter(g.get("result") for _, _, g in rows)
    per_deck: dict[str, dict[str, Any]] = {}
    for slug, name, g in rows:
        p = per_deck.setdefault(slug, {"slug": slug, "name": name, "games": 0, "wins": 0, "losses": 0})
        p["games"] += 1
        p["wins"] += g.get("result") == "win"
        p["losses"] += g.get("result") == "loss"
    return {
        "games": len(rows), "wins": results["win"], "losses": results["loss"], "draws": results["draw"],
        "per_deck": sorted(per_deck.values(), key=lambda p: -p["games"]),
        "history": [{"deck_slug": slug, "deck": name, **{k: g.get(k) for k in ("id", "played", "result", "turn", "note", "issues")}}
                    for _, (slug, name, g) in sorted(enumerate(rows), key=lambda r: (r[1][2].get("played") or "", r[0]), reverse=True)],
        "last": max((g.get("played") or "" for _, _, g in rows), default=None),
    }  # fmt: skip


def describe(o: dict[str, Any], all_games: list[tuple[str, str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    from .edhrec import commander_slug

    return {**o, "title": title(o), "tag_labels": [TAGS[t][0] for t in o.get("tags") or [] if t in TAGS],
            "record": record(o, all_games), "edhrec_url": f"https://edhrec.com/commanders/{commander_slug(o.get('commanders') or [])}"}  # fmt: skip


def all_opponents() -> list[dict[str, Any]]:
    """Every opponent deck with its record, most played (then most recently updated) first."""
    pool = _all_games()
    out = [describe(o, pool) for o in _read()]
    return sorted(out, key=lambda o: (o["record"]["games"], o["record"]["last"] or o.get("updated") or ""), reverse=True)


def relevant(deck: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    """Opponents that matter for a deck: faced with it, or playing at its table rule; else the most played ones."""
    pool = _all_games()
    items = [describe(o, pool) for o in _read()]
    if not items:
        return []
    slug = (deck or {}).get("slug")
    table = (deck or {}).get("table_rule")
    against = set((deck or {}).get("built_against") or [])

    def weight(o: dict[str, Any]) -> tuple[int, int]:
        with_deck = next((p["games"] for p in o["record"]["per_deck"] if p["slug"] == slug), 0)
        bonus = (5 if table and o.get("table_rule") == table else 0) + (4 if o["id"] in against else 0)
        return (with_deck * 3 + bonus, o["record"]["games"])

    picked = [o for o in items if weight(o)[0] > 0]
    if not picked:
        picked = [o for o in items if o["record"]["games"]] or items
    return sorted(picked, key=weight, reverse=True)[:limit]


def _line(o: dict[str, Any], deck_slug: str | None = None) -> str:
    parts = [title(o)]
    if o.get("player"):
        parts.append(f"spielt {o['player']}")
    if o.get("bracket"):
        parts.append(f"etwa Bracket {o['bracket']}")
    rec = o["record"]
    if rec["games"]:
        mine = next((p for p in rec["per_deck"] if p["slug"] == deck_slug), None)
        parts.append(f"deine Bilanz {rec['wins']}–{rec['losses']}" + (f" (mit diesem Deck {mine['wins']}–{mine['losses']})" if mine else ""))
    if o.get("tags"):
        parts.append("Merkmale: " + ", ".join(f"{TAGS[t][0]} → {TAGS[t][1]}" for t in o["tags"] if t in TAGS))
    notes = [n["text"] for n in (o.get("notes") or [])[-3:]]
    if notes:
        parts.append("aufgefallen: " + " | ".join(f"„{n}“" for n in notes))
    return "  - " + "; ".join(parts)


def own_deck_lines(limit: int = 15) -> list[str]:
    """The user's saved decks with level, power score and record – for the meta build prompt."""
    out = []
    for d in storage.list_decks()[:limit]:
        try:
            deck = storage.load(d["slug"])
        except FileNotFoundError:
            continue
        power = (((deck.get("validation") or {}).get("bracket") or {}).get("power") or {}).get("value")
        st = games.stats(games.games(d["slug"]))
        parts = [f"„{d['name']}“ ({' + '.join(d['commanders']) or '?'}, {d['level']}" + (f", Power {power}" if power else "") + ")"]
        if st["games"]:
            lost_to = Counter(o for g in games.games(d["slug"]) if g.get("result") == "loss" for o in g.get("opponents") or [])
            parts.append(f"Bilanz {st['wins']}–{st['losses']}" + (f", verloren gegen {', '.join(n for n, _ in lost_to.most_common(3))}" if lost_to else ""))
        if deck.get("description"):
            parts.append(deck["description"][:160])
        out.append("  - " + "; ".join(parts))
    return out


def meta_lines(opponent_ids: list[str] | None = None) -> list[str]:
    """The playgroup for a meta build: the chosen opponent decks (all when ``opponent_ids`` is empty)."""
    pool = _all_games()
    chosen = set(opponent_ids or [])
    items = [describe(o, pool) for o in _read() if not chosen or o["id"] in chosen]
    items.sort(key=lambda o: (o["record"]["games"], len(o.get("notes") or [])), reverse=True)
    return [_line(o) for o in items]


def prompt_lines(deck: dict[str, Any] | None, *, focus_id: str | None = None) -> list[str]:
    """Prompt lines about the user's opponents (empty without any)."""
    focus = get(focus_id) if focus_id else None
    items = relevant(deck)
    if focus:
        items = [describe(focus)] + [o for o in items if o["id"] != focus["id"]][:4]
    if not items:
        return []
    slug = (deck or {}).get("slug")
    lines = ["- Gegnerdecks des Nutzers (nur Commander und Beobachtungen, keine Listen):"]
    lines += [_line(o, slug) for o in items]
    if focus:
        lines.append(f"  - Schwerpunkt: das Deck soll besser gegen {title(focus)} werden.")
    lines.append("  - Berücksichtige diese Gegner bei der Kartenwahl (passende Interaktion und Antworten), ohne ein reines "
                 "Hate-Deck zu bauen. Typische Karten eines Gegner-Commanders findest du mit `edhrec_recommendations`; "
                 "alle Gegnerdecks mit Bilanz liefert `opponent_decks`.")  # fmt: skip
    return lines
