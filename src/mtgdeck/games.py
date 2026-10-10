"""Game log per deck (``decks/.games/<slug>.json``): result, opponents, turn, quick-pick problems,
best card and a note – plus statistics and a focus text for upgrade suggestions ("learn from
your games")."""

from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from . import storage
from .jsonstore import locked, read_json, write_json

GAMES_DIRNAME = ".games"
RESULTS = {"win": "Sieg", "loss": "Niederlage", "draw": "Unentschieden"}
# quick-pick problems: key -> (label, focus for upgrade suggestions)
ISSUES: dict[str, tuple[str, str]] = {
    "few_lands": ("zu wenige Länder", "konstanter Mana: mehr Länder oder günstige Ramp"),
    "flood": ("zu viele Länder gezogen", "weniger Länder, mehr Kartenzug"),
    "colors": ("falsche Farben / Farbprobleme", "bessere Manabasis mit mehr Farbquellen"),
    "slow": ("zu langsam", "schnellerer Start, niedrigere Manakurve"),
    "no_draw": ("Hand leer, zu wenig Kartenzug", "mehr Kartenzug"),
    "no_removal": ("keine Antwort auf Bedrohungen", "mehr flexibles Removal"),
    "wiped": ("Board Wipe hat mich zurückgeworfen", "Schutz und schnelle Erholung nach Board Wipes"),
    "commander_removed": ("Commander ständig entfernt", "Schutz für den Commander"),
    "no_win": ("kein Weg zu gewinnen", "klarere Siegbedingungen"),
    "combo": ("gegen eine Combo verloren", "Interaktion gegen Combos"),
    "fliers": ("gegen Flieger / Ausweichen verloren", "Antworten auf fliegende Kreaturen"),
    "too_strong": ("Deck war zu stark für die Runde", "passend zur Runde etwas abschwächen"),
}
# how the game was decided (optional) and quick-pick labels
HOW: dict[str, str] = {
    "combat": "Kampfschaden", "commander": "Commander-Schaden", "combo": "Combo",
    "alt": "alternative Siegbedingung", "concede": "Aufgabe", "other": "anders",
}  # fmt: skip


def _file(deck_slug: str) -> Path:
    return storage.DECKS_DIR / GAMES_DIRNAME / f"{storage.slug(deck_slug)}.json"


def games(deck_slug: str) -> list[dict[str, Any]]:
    """Games of a deck, oldest first."""
    items = read_json(_file(deck_slug), [])
    return items if isinstance(items, list) else []


def _write(deck_slug: str, items: list[dict[str, Any]]) -> None:
    path = _file(deck_slug)
    if not items:
        path.unlink(missing_ok=True)
        return
    write_json(path, items)


def add(deck_slug: str, *, result: str, opponents: list[str] | None = None, turn: int | None = None,
        issues: list[str] | None = None, mvp: str | None = None, note: str = "", version: int | None = None,
        played: str | None = None, entry_id: str | None = None, opponent_ids: list[str | None] | None = None,
        how: str | None = None, started: str | int | None = None) -> dict[str, Any]:  # fmt: skip
    if result not in RESULTS:
        raise ValueError(f"Ergebnis muss eines von {', '.join(RESULTS)} sein")
    unknown = [i for i in issues or [] if i not in ISSUES]
    if unknown:
        raise ValueError(f"Unbekannte Probleme: {', '.join(unknown)}")
    entry = {
        "id": entry_id or uuid.uuid4().hex[:10], "played": played or storage._now(), "result": result,
        "opponents": [o.strip() for o in opponents or [] if o.strip()][:5], "turn": turn,
        "issues": list(dict.fromkeys(issues or [])), "mvp": (mvp or "").strip() or None, "note": note.strip(), "version": version,
    }  # fmt: skip
    if opponent_ids and any(opponent_ids):
        entry["opponent_ids"] = list(opponent_ids)[: len(entry["opponents"])]
    if how:
        if how not in HOW:
            raise ValueError(f"Unbekannt, wie die Partie entschieden wurde: {how}")
        entry["how"] = how
    if started == "me" or (isinstance(started, int) and 0 <= started < len(entry["opponents"])):
        entry["started"] = started  # "me" or the index of the opponent who began
    with locked(_file(deck_slug)):
        _write(deck_slug, games(deck_slug) + [entry])
    return entry


def delete(deck_slug: str, game_id: str) -> int:
    with locked(_file(deck_slug)):
        return _delete(deck_slug, game_id)


def _delete(deck_slug: str, game_id: str) -> int:
    items = games(deck_slug)
    keep = [g for g in items if g.get("id") != game_id]
    _write(deck_slug, keep)
    return len(items) - len(keep)


def stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Win/loss record, most common problems, record per deck version, average end turn, best cards."""
    results = Counter(g.get("result") for g in items)
    issues = Counter(i for g in items for i in g.get("issues") or [] if i in ISSUES)
    per_version: dict[int, dict[str, int]] = {}
    for g in items:
        if g.get("version"):
            v = per_version.setdefault(int(g["version"]), {"games": 0, "wins": 0})
            v["games"] += 1
            v["wins"] += g.get("result") == "win"
    turns = [int(g["turn"]) for g in items if g.get("turn")]
    return {
        "games": len(items), "wins": results["win"], "losses": results["loss"], "draws": results["draw"],
        "win_rate": round(results["win"] / len(items), 2) if items else None,
        "avg_turn": round(sum(turns) / len(turns), 1) if turns else None,
        "issues": [{"key": k, "label": ISSUES[k][0], "count": n} for k, n in issues.most_common()],
        "per_version": [{"version": v, **per_version[v]} for v in sorted(per_version)],
        "mvps": [{"name": n, "count": c} for n, c in Counter(g["mvp"] for g in items if g.get("mvp")).most_common(5)],
        "opponents": [{"name": n, "count": c} for n, c in Counter(o for g in items for o in g.get("opponents") or []).most_common(5)],
    }  # fmt: skip


def learn_focus(items: list[dict[str, Any]], top: int = 3) -> str:
    """Focus text for upgrade suggestions from the most common problems (empty without problems)."""
    common = stats(items)["issues"][:top]
    if not common:
        return ""
    return "Aus den Partien: " + "; ".join(f"{ISSUES[i['key']][1]} ({i['count']}× „{i['label']}“)" for i in common)


def summary(deck_slug: str) -> dict[str, Any]:
    """Games plus statistics – what the MCP tool and the GUI return."""
    items = games(deck_slug)
    return {"games": items, "stats": stats(items), "learn_focus": learn_focus(items),
            "issue_labels": {k: v[0] for k, v in ISSUES.items()}, "result_labels": RESULTS, "how_labels": HOW}  # fmt: skip
