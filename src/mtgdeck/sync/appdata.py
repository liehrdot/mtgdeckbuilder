"""Read model for the phone app ("Am Tisch"): one compact JSON built from the synced documents.

``snapshot(docs)`` takes ``{logical path: parsed document}`` (decks, game logs, opponents) and returns what the phone
shows: decks with Rule 0, guide, card list (names + categories; card data comes from Scryfall on the phone) and record,
opponents with their record against the user, all games (newest first), the latest conversations with Claude
(„Frag Claude“, also from the PC) and the quick-pick lists. Pure – no files, no
network – so the sync server can build it from its store and tests from plain dicts.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .. import rule0
from ..games import HOW, ISSUES, RESULTS
from ..opponents import TAGS, record as opponent_record, title as opponent_title
from ..storage import level_text
from .files import DECK_RE

GAMES_RE = re.compile(r"decks/\.games/([^/.][^/]*)\.json")
CHATS_RE = re.compile(r"decks/\.chats/([0-9a-f]{12})\.json")
CHATS_MAX = 30
CHAT_MESSAGES_MAX = 20
OPPONENTS_PATH = "decks/.opponents.json"
GAMES_MAX = 400
NOTES_MAX = 30
VERSION = 1


def _record(items: list[dict[str, Any]]) -> dict[str, Any]:
    results = Counter(g.get("result") for g in items)
    return {"games": len(items), "wins": results["win"], "losses": results["loss"], "draws": results["draw"],
            "last": max((g.get("played") or "" for g in items), default=None)}  # fmt: skip


def _guide(guide: Any) -> dict[str, Any] | None:
    if not isinstance(guide, dict) or not guide.get("plan"):
        return None
    keys = ("plan", "early", "mid", "late", "mulligan", "win_conditions", "watch_out", "tips")
    out = {k: guide[k] for k in keys if guide.get(k)}
    out["key_cards"] = [{"name": k.get("name"), "why": k.get("why")} for k in guide.get("key_cards") or []
                        if isinstance(k, dict) and k.get("name")]  # fmt: skip
    return out


def _deck(slug: str, d: dict[str, Any], games: list[dict[str, Any]]) -> dict[str, Any]:
    v = d.get("validation") or {}
    r0 = rule0.build({**d, "slug": slug})
    return {
        "slug": slug, "name": d.get("name") or slug, "commanders": d.get("commanders") or [],
        "colors": v.get("color_identity") or "", "bracket": d.get("bracket"),
        "tier": (d.get("power_profile") or {}).get("tier"), "level": level_text(d), "proxy": bool(d.get("proxy")),
        "legal": v.get("legal"), "description": rule0._first_sentence(d.get("description") or d.get("strategy") or "", 220),
        "version": d.get("version"), "updated": d.get("updated"),
        "rule0": {"rows": r0["rows"], "text": r0["text"]}, "guide": _guide(d.get("guide")),
        "cards": [{"name": c["name"], "qty": int(c.get("qty") or 1), "category": c.get("category") or ""}
                  for c in d.get("cards") or [] if isinstance(c, dict) and c.get("name")],
        "record": _record(games),
    }  # fmt: skip


def _chat(chat_id: str, c: dict[str, Any]) -> dict[str, Any]:
    msgs = []
    for m in [m for m in c.get("messages") or [] if isinstance(m, dict) and m.get("id")][-CHAT_MESSAGES_MAX:]:
        out = {"id": m["id"], "asked": m.get("asked"), "question": m.get("question") or "", "answer": m.get("answer") or None,
               "deep": bool(m.get("deep")), "source": m.get("source") or "pc"}  # fmt: skip
        if not out["answer"]:
            out["status"] = m.get("status") or "waiting"
            if m.get("error"):
                out["error"] = m["error"]
        decks = {s: (v or {}).get("name") or s for s, v in (m.get("decks") or {}).items()} if isinstance(m.get("decks"), dict) else {}
        if decks:
            out["decks"] = decks
        msgs.append(out)
    return {"id": chat_id, "title": c.get("title") or "", "created": c.get("created"), "updated": c.get("updated"), "messages": msgs}


def snapshot(docs: dict[str, Any]) -> dict[str, Any]:
    decks_raw = {m.group(1): v for p, v in docs.items() if (m := DECK_RE.fullmatch(p)) and isinstance(v, dict)}
    games: list[dict[str, Any]] = []
    for path, value in docs.items():
        m = GAMES_RE.fullmatch(path)
        if m and isinstance(value, list):
            games += [{**g, "deck": m.group(1)} for g in value if isinstance(g, dict) and g.get("id")]
    games.sort(key=lambda g: g.get("played") or "", reverse=True)

    decks = [_deck(slug, d, [g for g in games if g["deck"] == slug]) for slug, d in decks_raw.items()]
    decks.sort(key=lambda d: (d["record"]["last"] or "", d["updated"] or ""), reverse=True)

    names = {d["slug"]: d["name"] for d in decks}
    all_games = [(g["deck"], names.get(g["deck"], g["deck"]), g) for g in games]
    raw = (docs.get(OPPONENTS_PATH) or {}).get("opponents") if isinstance(docs.get(OPPONENTS_PATH), dict) else []
    opponents = []
    for o in raw or []:
        if not isinstance(o, dict) or not o.get("id"):
            continue
        rec = opponent_record(o, all_games)
        opponents.append({
            "id": o["id"], "title": opponent_title(o), "label": o.get("label") or "", "player": o.get("player") or "",
            "commanders": o.get("commanders") or [], "colors": "".join(o.get("color_identity") or []),
            "bracket": o.get("bracket"), "tags": [t for t in o.get("tags") or [] if t in TAGS],
            "notes": sorted(o.get("notes") or [], key=lambda n: n.get("at") or "", reverse=True)[:NOTES_MAX],
            "record": {k: rec[k] for k in ("games", "wins", "losses", "draws", "last", "per_deck")},
            "updated": o.get("updated"), "created": o.get("created"),
        })  # fmt: skip
    opponents.sort(key=lambda o: (o["record"]["last"] or "", o["updated"] or ""), reverse=True)

    chats = [_chat(m.group(1), v) for p, v in docs.items() if (m := CHATS_RE.fullmatch(p)) and isinstance(v, dict)]
    chats = sorted((c for c in chats if c["messages"]), key=lambda c: c["updated"] or "", reverse=True)[:CHATS_MAX]

    return {
        "version": VERSION, "decks": decks, "opponents": opponents, "games": games[:GAMES_MAX], "chats": chats,
        "players": sorted({o["player"] for o in opponents if o["player"]}, key=str.lower),
        "options": {
            "results": RESULTS, "issues": [[k, v[0]] for k, v in ISSUES.items()],
            "tags": [[k, v[0]] for k, v in TAGS.items()], "how": [[k, v] for k, v in HOW.items()],
        },
    }  # fmt: skip
