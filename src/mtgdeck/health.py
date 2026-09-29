"""Deck check as a traffic light: one row per area (lands, ramp, draw, removal, wipes, curve, win
conditions) with the count, the guideline, a status green/yellow/red and a beginner explanation.

Works on a saved deck with its stored validation (``deck["validation"]["stats"]``) – no network.
The guidelines follow ``references/deck-template.md``; the role counts are heuristics (Tagger tags
or Oracle-text regexes), so the wording says "erkannt".
"""

from __future__ import annotations

from typing import Any

GREEN, YELLOW, RED = "green", "yellow", "red"
_RANK = {GREEN: 0, YELLOW: 1, RED: 2}


def _band(value: float, green: float, yellow: float) -> str:
    """Higher is better: >= green is green, >= yellow is yellow, else red."""
    return GREEN if value >= green else YELLOW if value >= yellow else RED


def land_target(avg_cmc: float, ramp: int) -> int:
    if avg_cmc <= 2.5 and ramp >= 12:
        return 34
    if avg_cmc >= 3.6:
        return 38
    if avg_cmc >= 3.3:
        return 37
    return 36


def _item(key: str, label: str, status: str, value: Any, target: str, text: str, why: str,
          fix: dict[str, Any] | None = None, cards: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    return {"key": key, "label": label, "status": status, "value": value, "target": target, "text": text,
            "why": why, "fix": fix, "cards": sorted(cards or [])}  # fmt: skip


def check(deck: dict[str, Any]) -> dict[str, Any]:
    """``{"status", "items": [{key, label, status, value, target, text, why, fix, cards}], "summary"}``.

    ``fix`` is ``{"role": ...}`` (candidates for that role can be added without AI) and/or
    ``{"focus": ...}`` (a focus text for AI upgrade suggestions)."""
    validation = deck.get("validation") or {}
    stats = validation.get("stats") or {}
    roles: dict[str, list[str]] = stats.get("roles") or {}
    lands = int((stats.get("types") or {}).get("Land", 0))
    avg = float(stats.get("avg_cmc_nonland") or 0)
    bracket = int(deck.get("bracket") or 3)
    items: list[dict[str, Any]] = []

    ramp = roles.get("ramp", [])
    target = land_target(avg, len(ramp))
    off = abs(lands - target)
    status = GREEN if off <= 1 else YELLOW if off <= 3 else RED
    text = (f"{lands} Länder – passt." if status == GREEN
            else f"{lands} Länder – {'zu wenige' if lands < target else 'eher zu viele'}, Richtwert {target}.")  # fmt: skip
    items.append(_item(
        "lands", "Länder", status, lands, f"{target - 1}–{target + 1}", text,
        "Ohne genug Länder bleibst du mit teuren Karten auf der Hand sitzen; mit zu vielen ziehst du später nur noch Länder. "
        f"Der Richtwert hängt vom durchschnittlichen Manawert ({avg:.2f}) und deiner Ramp ab.",
        {"focus": f"Manabasis: auf etwa {target} Länder kommen"} if status != GREEN else None,
    ))  # fmt: skip

    items.append(_item(
        "ramp", "Ramp", _band(len(ramp), 10, 7), len(ramp), "10–12",
        f"{len(ramp)} Karten beschleunigen dein Mana.",
        "Ramp (Manasteine, Land-Suche) bringt dich schneller zu deinem Commander und deinen starken Karten – "
        "in Commander mit 40 Leben und vier Spielern besonders wichtig.",
        {"role": "ramp", "focus": "mehr Ramp"}, ramp,
    ))  # fmt: skip

    draw = roles.get("card_draw", [])
    items.append(_item(
        "draw", "Kartenzug", _band(len(draw), 10, 7), len(draw), "≈ 10",
        f"{len(draw)} Karten ziehen dir zusätzliche Karten.",
        "Wer mehr Karten zieht, hat mehr Möglichkeiten. Ohne Kartenzug ist die Hand nach ein paar Zügen leer; "
        "wiederholbare Quellen (jede Runde eine Karte) sind besser als einmalige.",
        {"role": "card_draw", "focus": "mehr Kartenzug"}, draw,
    ))  # fmt: skip

    removal = sorted(set(roles.get("removal", [])) | set(roles.get("counterspell", [])))
    items.append(_item(
        "removal", "Removal", _band(len(removal), 8, 5), len(removal), "8–10",
        f"{len(removal)} Karten entfernen gezielt Bedrohungen.",
        "Mit Removal hältst du gefährliche Karten der Gegner auf – etwa einen Commander, der sonst das Spiel gewinnt. "
        "Flexible Antworten (jede bleibende Karte, zu Spontanzauber-Tempo) sind am wertvollsten.",
        {"role": "removal", "focus": "mehr gezieltes Removal"}, removal,
    ))  # fmt: skip

    wipes = roles.get("board_wipe", [])
    items.append(_item(
        "wipes", "Board Wipes", GREEN if len(wipes) >= 2 else YELLOW, len(wipes), "2–4",
        f"{len(wipes)} Karten räumen das Spielfeld." + (" Für Decks mit vielen eigenen Kreaturen kann das Absicht sein." if len(wipes) < 2 else ""),
        "Ein Board Wipe ist die Notbremse, wenn ein Gegner zu viel aufgebaut hat. Decks mit vielen eigenen Kreaturen "
        "spielen bewusst weniger davon.",
        {"role": "board_wipe", "focus": "1–2 Board Wipes"}, wipes,
    ))  # fmt: skip

    curve = stats.get("mana_curve") or {}
    big = int(curve.get("6", 0)) + int(curve.get("7+", 0))
    limit = 2.6 if bracket >= 4 else 3.3
    status = GREEN if avg <= limit else YELLOW if avg <= limit + 0.5 else RED
    if big > 12 and status == GREEN:
        status = YELLOW
    items.append(_item(
        "curve", "Manakurve", status, round(avg, 2), f"≤ {limit:.1f}".replace(".", ","),
        f"Ø Manawert {avg:.2f}".replace(".", ",") + f", {big} Karten kosten 6 oder mehr.",
        "Günstige Karten kannst du früh und mehrere pro Zug spielen. Ist die Kurve zu hoch, passiert in den ersten "
        "Zügen nichts, während die Gegner schon angreifen.",
        {"focus": "Manakurve senken: teure Karten durch günstigere ersetzen"} if status != GREEN else None,
    ))  # fmt: skip

    wincons = [c["name"] for c in deck.get("cards", []) if c.get("category") == "Win Condition"]
    combos = (validation.get("combos") or {}).get("included") or []
    n_win = len(wincons) + len(combos)
    categorized = sum(1 for c in deck.get("cards", []) if c.get("category") and c.get("category") != "Land")
    status = GREEN if n_win >= 2 else YELLOW if n_win == 1 or categorized < 20 else RED
    text = f"{len(wincons)} Karten als Siegbedingung markiert" + (f", {len(combos)} Combo(s)" if combos else "") + "."
    if not n_win and categorized < 20:
        text += " Die Karten haben kaum Kategorien – markiere deine Gewinnkarten als „Win Condition“."
    items.append(_item(
        "wincons", "Siegbedingungen", status, n_win, "2–4", text,
        "Irgendwie muss das Spiel enden: große Angreifer, Lebensentzug an alle, eine Combo. Ohne klare Siegwege "
        "ziehen sich Partien und du verlierst gegen Decks, die schneller zum Punkt kommen.",
        {"focus": "klarere Siegbedingungen"} if status != GREEN else None, wincons,
    ))  # fmt: skip

    worst = max((i["status"] for i in items), key=_RANK.__getitem__, default=GREEN)
    n_bad = sum(1 for i in items if i["status"] != GREEN)
    summary = ("Alles im grünen Bereich." if not n_bad
               else f"{n_bad} {'Bereich braucht' if n_bad == 1 else 'Bereiche brauchen'} Aufmerksamkeit.")  # fmt: skip
    return {"status": worst, "items": items, "summary": summary}
