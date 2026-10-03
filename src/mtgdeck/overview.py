"""Everything at a glance – the user's decks, games, opponents, table rules and collection.

Used by the app chat („Frag Claude“: prompt lines) and the MCP tool ``app_overview``. Only stored data is
read (no network): power score, health and record come from what validation and the game log saved.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import collection, games, health, opponents, storage, tablerules
from .fmt import money, num


def deck_row(deck: dict[str, Any]) -> dict[str, Any]:
    """One deck in a few numbers: level, power, legality, price, health, record, coverage by the collection."""
    v = deck.get("validation") or {}
    power = ((v.get("bracket") or {}).get("power") or {}).get("value")
    hc = health.check(deck)
    weak = [{"area": i["label"], "status": i["status"], "text": i["text"]} for i in hc["items"] if i["status"] != health.GREEN]
    items = games.games(deck["slug"])
    st = games.stats(items)
    lost_to = Counter(o for g in items if g.get("result") == "loss" for o in g.get("opponents") or [])
    try:
        missing = sum(collection.missing_counts(deck).values())
    except Exception:  # an unreadable collection must not break the overview
        missing = None
    plan = deck.get("upgrade_plan") or {}
    names = {c["name"] for c in deck.get("cards", [])}
    open_stages = sum(1 for s in plan.get("stages") or [] if not all(u.get("add") in names and u.get("remove") not in names
                                                                     for u in s.get("upgrades") or []))  # fmt: skip
    rule = tablerules.get(deck.get("table_rule"))
    return {
        "slug": deck["slug"], "name": deck.get("name", deck["slug"]), "commanders": deck.get("commanders", []),
        "level": storage.level_text(deck), "bracket": deck.get("bracket"), "power": power,
        "style": (deck.get("power_profile") or {}).get("style") or None,
        "legal": v.get("legal"), "errors": len(v.get("errors") or []),
        "price": v.get("price_total"), "budget": deck.get("budget"), "currency": deck.get("currency", "eur"),
        "proxy": bool(deck.get("proxy")), "version": deck.get("version"), "updated": deck.get("updated"),
        "description": (deck.get("description") or "")[:240],
        "table_rule": rule["name"] if rule else None,
        "health": {"status": hc["status"], "summary": hc["summary"], "weak": weak},
        "games": {k: st[k] for k in ("games", "wins", "losses", "draws", "win_rate", "avg_turn")}
                 | {"issues": [i["label"] for i in st["issues"][:3]], "lost_to": [n for n, _ in lost_to.most_common(3)]},
        "missing_cards": missing,
        "upgrade_plan_open_stages": open_stages if plan else None,
        "has_guide": bool(deck.get("guide")),
    }  # fmt: skip


def build() -> dict[str, Any]:
    """The whole app in one dict (decks newest first)."""
    decks = []
    for d in storage.list_decks():
        if d.get("damaged"):
            decks.append({"slug": d["slug"], "name": d["name"], "damaged": True})
            continue
        try:
            decks.append(deck_row(storage.load(d["slug"])))
        except (FileNotFoundError, ValueError):
            continue
    opps = [{"id": o["id"], "title": o["title"], "bracket": o.get("bracket"), "tags": o["tag_labels"],
             "record": {k: o["record"][k] for k in ("games", "wins", "losses")},
             "notes": [n["text"] for n in (o.get("notes") or [])[-2:]]} for o in opponents.all_opponents()]  # fmt: skip
    rules = [{"id": r["id"], "name": r["name"], "summary": r["summary"]} for r in tablerules.all_sets()]
    return {"decks": decks, "opponents": opps, "table_rules": rules, "collection": collection.summary()}


def _deck_line(d: dict[str, Any]) -> str:
    if d.get("damaged"):
        return f"- {{{{{d['slug']}}}}}: Datei beschädigt"
    parts = [f"{' + '.join(d['commanders']) or '?'}", d["level"]]
    if d["power"] is not None:
        parts.append(f"Power {num(d['power'], 1)}")
    parts.append("legal" if d["legal"] else f"nicht legal ({d['errors']} Fehler)")
    if d["price"] is not None:
        parts.append(money(d["price"], d["currency"]) + (" (Proxy)" if d["proxy"] else "")
                     + (f" / Budget {money(d['budget'], d['currency'], 0)}" if d["budget"] else ""))  # fmt: skip
    if d["table_rule"]:
        parts.append(f"Tischregel {d['table_rule']}")
    g = d["games"]
    if g["games"]:
        rec = f"Bilanz {g['wins']}–{g['losses']}" + (f"–{g['draws']}" if g["draws"] else "") + f" in {g['games']} Partien"
        if g["avg_turn"]:
            rec += f", Ø Zug {num(g['avg_turn'], 1)}"
        if g["issues"]:
            rec += ", Probleme: " + ", ".join(g["issues"])
        if g["lost_to"]:
            rec += ", verloren gegen " + ", ".join(g["lost_to"])
        parts.append(rec)
    else:
        parts.append("noch keine Partien")
    weak = [f"{w['area']} {'rot' if w['status'] == health.RED else 'gelb'}" for w in d["health"]["weak"]]
    if weak:
        parts.append("Deck-Check: " + ", ".join(weak))
    if d["missing_cards"]:
        parts.append(f"{d['missing_cards']} Karten fehlen in der Sammlung")
    if d["upgrade_plan_open_stages"]:
        parts.append(f"Upgrade-Plan: {d['upgrade_plan_open_stages']} offene Stufen")
    line = f"- {{{{{d['slug']}}}}} „{d['name']}“ (v{d['version']}): " + "; ".join(parts)
    return line + (f". {d['description']}" if d["description"] else "")


def prompt_lines(data: dict[str, Any] | None = None) -> list[str]:
    """The overview as compact German prompt lines."""
    data = data or build()
    lines = [f"Gespeicherte Decks ({len(data['decks'])}):"]
    lines += [_deck_line(d) for d in data["decks"]] or ["- noch keine"]
    if data["opponents"]:
        lines += ["", f"Gegnerdecks ({len(data['opponents'])}):"]
        for o in data["opponents"][:20]:
            rec = o["record"]
            bits = [f"Bilanz dagegen {rec['wins']}–{rec['losses']}" if rec["games"] else "noch nicht festgehalten"]
            if o["tags"]:
                bits.append(", ".join(o["tags"]))
            if o["notes"]:
                bits.append(" | ".join(f"„{n}“" for n in o["notes"]))
            lines.append(f"- {o['title']}" + (f" (Bracket {o['bracket']})" if o.get("bracket") else "") + ": " + "; ".join(bits))
    if data["table_rules"]:
        lines += ["", "Tischregeln:"]
        lines += [f"- {r['name']} (`{r['id']}`): " + "; ".join(r["summary"]) for r in data["table_rules"]]
    c = data["collection"]
    if c["entries"]:
        lines += ["", f"Sammlung: {c['cards']} Karten ({c['unique']} verschiedene, davon {c['proxy']} Proxys), "
                      f"Wert ca. {money(c['value_eur'], 'eur')}."]  # fmt: skip
    return lines
