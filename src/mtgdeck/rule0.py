"""Rule-0 text: what you tell the table before the game ("Rule 0" conversation), built from the
stored deck check without AI – level, play style, Game Changers, combos, tutors, extra turns,
land destruction, expected speed, proxies and house rules.
"""

from __future__ import annotations

from typing import Any

from . import brackets, storage


def _names(items: list[str], limit: int = 6) -> str:
    return ", ".join(items[:limit]) + (f" und {len(items) - limit} weitere" if len(items) > limit else "")


def _first_sentence(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    cut = next((i + 1 for i, ch in enumerate(text) if ch in ".!?" and i > 20), len(text))
    text = text[:cut]
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _house_rules(profile: dict[str, Any]) -> list[str]:
    out = []
    if profile.get("max_game_changers") is not None:
        out.append(f"max. {profile['max_game_changers']} Game Changer")
    if profile.get("max_tutors") is not None:
        out.append(f"max. {profile['max_tutors']} Tutoren")
    for key, label in (("allow_two_card_combos", "2-Karten-Combos"), ("allow_extra_turns", "Extra-Züge"),
                       ("allow_mass_land_denial", "Massen-Landzerstörung")):  # fmt: skip
        if profile.get(key) is False:
            out.append(f"keine {label}")
    return out


def build(deck: dict[str, Any]) -> dict[str, Any]:
    """``{"title", "rows": [{"label", "value", "flag"}], "text"}`` – ``flag`` marks rows the table
    should know about (Game Changers, combos, extra turns, land destruction); ``text`` is the
    copyable plain-text version."""
    v = deck.get("validation") or {}
    b = v.get("bracket") or {}
    number = int(deck.get("bracket") or b.get("target") or 3)
    rules = brackets.BY_NUMBER.get(number, brackets.BY_NUMBER[3])
    power = b.get("power") or {}
    profile = deck.get("power_profile") or {}
    rows: list[dict[str, Any]] = []

    def row(label: str, value: str, flag: bool = False) -> None:
        rows.append({"label": label, "value": value, "flag": flag})

    level = f"{storage.level_text(deck)} ({rules['name']})"
    if power.get("value"):
        level += f" · Einschätzung {power['value']:.1f}".replace(".", ",")
    row("Stufe", level)
    row("Commander", " + ".join(deck.get("commanders") or []) or "?")
    style = _first_sentence(deck.get("description") or deck.get("strategy") or "")
    if profile.get("style"):
        style = f"{style} Stimmung: {profile['style']}.".strip()
    if style:
        row("Spielweise", style)
    row("Tempo", f"frühestes Spielende nach Bracket: {rules['earliest_game_end']}")

    gc = b.get("game_changers") or []
    row("Game Changer", f"{len(gc)}: {_names(gc)}" if gc else "keine", bool(gc))
    combos = b.get("two_card_combos") or []
    if combos:
        text = "; ".join(" + ".join(c.get("cards") or ["?"]) + (f" ({', '.join(p for p in c['produces'] if p)[:60]})" if any(c.get("produces") or []) else "")
                         for c in combos[:4])  # fmt: skip
        row("2-Karten-Combos", text, True)
    else:
        row("2-Karten-Combos", "keine")
    tutors = b.get("tutors") or []
    row("Tutoren", f"{len(tutors)}: {_names(tutors, 4)}" if tutors else "keine", len(tutors) >= 4)
    extra = b.get("extra_turns") or []
    row("Extra-Züge", _names(extra) if extra else "keine", bool(extra))
    mld = b.get("mass_land_denial") or []
    row("Land-Zerstörung", _names(mld) if mld else "keine", bool(mld))
    if deck.get("proxy"):
        row("Proxys", "Proxy-Deck – enthält selbst gedruckte Karten", True)
    table = v.get("table_rule") or {}
    if table.get("name"):
        row("Tischregel", f"„{table['name']}“ – " + ("eingehalten" if table.get("compliant") else
            f"{len(table.get('violations') or [])} Verstoß/Verstöße"), not table.get("compliant"))  # fmt: skip
    rules_text = _house_rules(profile)
    if rules_text:
        row("Hausregeln", ", ".join(rules_text))

    title = f"{deck.get('name', deck.get('slug', 'Deck'))} – Rule 0"
    lines = [title] + [f"{r['label']}: {r['value']}" for r in rows] + ["Passt das für eure Runde?"]
    return {"title": title, "rows": rows, "text": "\n".join(lines)}
