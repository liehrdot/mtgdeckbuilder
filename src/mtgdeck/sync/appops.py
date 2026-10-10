"""Operations from the phone app ("Am Tisch") on the synced documents – written exactly as the desktop app would:
game entries like ``games.add``, opponent decks and their linking like ``opponents.link_game``.

An operation is ``{"id", "type", "payload", "at"}``; ``apply(op, docs)`` takes the documents it needs
(``paths(op)``) and returns the changed ones. Pure: the sync server reads and writes the store around it, and
remembers applied ids, so an operation sent twice (the phone retries after a lost answer) changes nothing.

Types: ``game.add`` · ``game.delete`` · ``opponent.add`` · ``opponent.update`` · ``opponent.note`` ·
``opponent.note_delete`` · ``opponent.delete`` · ``chat.ask`` (a question for Claude: an open message in the
conversation, answered later by a PC – see ``answer_chat``) · ``chat.cancel``.
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from ..games import HOW, ISSUES, RESULTS
from ..opponents import MAX_NOTES, TAGS

OPPONENTS = "decks/.opponents.json"
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
ISO_RE = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+(Z|[+-]\d\d:\d\d)?")
TYPES = ("game.add", "game.delete", "opponent.add", "opponent.update", "opponent.note", "opponent.note_delete", "opponent.delete",
         "chat.ask", "chat.cancel")
CHAT_RE = re.compile(r"[0-9a-f]{12}")  # like chat.py
MSG_RE = re.compile(r"[0-9a-f]{10}")
TITLE_LEN = 70  # like chat.py


class OpError(ValueError):
    """The operation cannot be applied; the message is for the user (German)."""


def _deck_slug(payload: dict[str, Any], key: str = "deck") -> str:
    slug = str(payload.get(key) or "")
    if not SLUG_RE.fullmatch(slug):
        raise OpError("Unbekanntes Deck")
    return slug


def _id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise OpError(f"Ungültige Kennung: {what}")
    return value


def paths(op: dict[str, Any]) -> list[str]:
    """The documents an operation reads or writes."""
    t, p = op.get("type"), op.get("payload") or {}
    if t not in TYPES:
        raise OpError(f"Unbekannte Aktion: {t}")
    if t == "game.add":
        slug = _deck_slug(p.get("game") or {})
        return [f"decks/{slug}.json", f"decks/.games/{slug}.json", OPPONENTS]
    if t == "game.delete":
        return [f"decks/.games/{_deck_slug(p)}.json"]
    if t.startswith("chat."):
        return [chat_path(p.get("chat_id"))]
    return [OPPONENTS]


def chat_path(chat_id: Any) -> str:
    if not isinstance(chat_id, str) or not CHAT_RE.fullmatch(chat_id):
        raise OpError("Unbekanntes Gespräch")
    return f"decks/.chats/{chat_id}.json"


def _chat_title(question: str) -> str:
    t = " ".join(question.split())
    return t if len(t) <= TITLE_LEN else t[: TITLE_LEN - 2].rstrip() + " …"


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _opponents(doc: Any) -> dict[str, Any]:
    items = (doc or {}).get("opponents") if isinstance(doc, dict) else None
    return {"opponents": [o for o in items or [] if isinstance(o, dict) and o.get("id")]}


def _new_opponent(fields: dict[str, Any], at: str) -> dict[str, Any]:
    commanders = [_text(c, 120) for c in fields.get("commanders") or [] if _text(c, 120)][:2]
    if not commanders:
        raise OpError("Bitte den Commander des Gegners angeben.")
    colors = fields.get("colors") or ""
    return {
        "id": _id(fields.get("id") or uuid.uuid4().hex[:8], "Gegner"), "commanders": commanders,
        "label": _text(fields.get("label"), 80), "player": _text(fields.get("player"), 80), "bracket": None, "table_rule": None,
        "tags": [t for t in dict.fromkeys(fields.get("tags") or []) if t in TAGS], "notes": [],
        "color_identity": [c for c in "WUBRG" if c in str(colors)], "image": fields.get("image") or None,
        "created": at, "updated": at,
    }  # fmt: skip


def _note(text: str, at: str, note_id: str, game_id: str | None = None, deck_slug: str | None = None) -> dict[str, Any]:
    return {"id": note_id, "text": text[:1000], "at": at, "game_id": game_id, "deck_slug": deck_slug}


def _match(items: list[dict[str, Any]], slot: dict[str, Any]) -> dict[str, Any] | None:
    """Like ``opponents.link_game``: by id, else the newest opponent deck led by this commander."""
    if slot.get("id"):
        o = next((x for x in items if x["id"] == slot["id"]), None)
        if o is not None:
            return o
    name = _text(slot.get("commander"), 120).lower()
    hits = [x for x in items if name and name in {n.lower() for n in x.get("commanders") or []}]
    return max(hits, key=lambda x: x.get("updated") or "") if hits else None


def apply(op: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any]:
    """Apply ``op`` to ``docs`` (``{path: value or None}`` for ``paths(op)``); returns ``{path: new value}``."""
    t, p, at = op["type"], op.get("payload") or {}, str(op.get("at") or "")
    if not ISO_RE.fullmatch(at):
        raise OpError("Ungültiger Zeitpunkt")
    out: dict[str, Any] = {}
    if t == "game.add":
        g = p.get("game") or {}
        slug = _deck_slug(g)
        deck = docs.get(f"decks/{slug}.json")
        if not isinstance(deck, dict):
            raise OpError("Dieses Deck gibt es nicht mehr – am PC gelöscht?")
        game_id = _id(g.get("id"), "Partie")
        games = [x for x in docs.get(f"decks/.games/{slug}.json") or [] if isinstance(x, dict)]
        if any(x.get("id") == game_id for x in games):
            return out  # already there (sent twice)
        result = g.get("result")
        if result not in RESULTS:
            raise OpError("Ergebnis fehlt")
        issues = [i for i in dict.fromkeys(g.get("issues") or []) if i in ISSUES]
        turn = g.get("turn")
        turn = int(turn) if isinstance(turn, int) and 1 <= turn <= 60 else None
        opp_doc = _opponents(docs.get(OPPONENTS))
        items = opp_doc["opponents"]
        names: list[str] = []
        ids: list[str | None] = []
        changed = False
        for slot in (p.get("opponents") or [])[:5]:
            if not isinstance(slot, dict):
                continue
            o = _match(items, slot)
            if o is None and _text(slot.get("commander"), 120):
                o = _new_opponent({"id": slot.get("new_id"), "commanders": [slot.get("commander")], "colors": slot.get("colors"),
                                   "image": slot.get("image")}, at)  # fmt: skip
                items.append(o)
            if o is None:
                continue
            note = _text(slot.get("note"), 1000)
            if note:
                o["notes"] = ((o.get("notes") or []) + [_note(note, at, uuid.uuid4().hex[:8], game_id, slug)])[-MAX_NOTES:]
            o["updated"] = at
            changed = True
            names.append(_text(slot.get("commander"), 120) or o["commanders"][0])
            ids.append(o["id"])
        played = g.get("played") if isinstance(g.get("played"), str) and ISO_RE.fullmatch(g["played"]) else at  # undo of a delete keeps the date
        entry: dict[str, Any] = {
            "id": game_id, "played": played, "result": result, "opponents": names, "turn": turn, "issues": issues,
            "mvp": _text(g.get("mvp"), 120) or None, "note": _text(g.get("note"), 2000), "version": deck.get("version"),
        }  # fmt: skip
        if any(ids):
            entry["opponent_ids"] = ids
        if g.get("how") in HOW:
            entry["how"] = g["how"]
        started = g.get("started")
        if started == "me" or (isinstance(started, int) and 0 <= started < len(names)):
            entry["started"] = started
        out[f"decks/.games/{slug}.json"] = games + [entry]
        if changed:
            out[OPPONENTS] = opp_doc
        return out
    if t.startswith("chat."):
        return _chat_op(t, p, docs, at)
    if t == "game.delete":
        path = f"decks/.games/{_deck_slug(p)}.json"
        games = [x for x in docs.get(path) or [] if isinstance(x, dict)]
        keep = [x for x in games if x.get("id") != p.get("id")]
        if len(keep) != len(games):
            out[path] = keep
        return out

    opp_doc = _opponents(docs.get(OPPONENTS))
    items = opp_doc["opponents"]
    if t == "opponent.add":
        o = _new_opponent(p.get("opponent") or {}, at)
        if any(x["id"] == o["id"] for x in items):
            return out
        note = _text(p.get("note"), 1000)
        if note:
            o["notes"] = [_note(note, at, _id(p.get("note_id") or uuid.uuid4().hex[:8], "Notiz"))]
        items.append(o)
        out[OPPONENTS] = opp_doc
        return out
    o = next((x for x in items if x["id"] == p.get("id")), None)
    if o is None:
        if t in ("opponent.delete", "opponent.note_delete"):
            return out  # gone already
        raise OpError("Dieses Gegnerdeck gibt es nicht mehr")
    if t == "opponent.update":
        if "tags" in p:
            o["tags"] = [x for x in dict.fromkeys(p.get("tags") or []) if x in TAGS]
        for key in ("player", "label"):
            if key in p:
                o[key] = _text(p.get(key), 80)
        o["updated"] = at
    elif t == "opponent.note":
        note_id = _id(p.get("note_id"), "Notiz")
        text = _text(p.get("text"), 1000)
        if not text or any(n.get("id") == note_id for n in o.get("notes") or []):
            return out
        o["notes"] = ((o.get("notes") or []) + [_note(text, at, note_id)])[-MAX_NOTES:]
        o["updated"] = at
    elif t == "opponent.note_delete":
        before = len(o.get("notes") or [])
        o["notes"] = [n for n in o.get("notes") or [] if n.get("id") != p.get("note_id")]
        if len(o["notes"]) == before:
            return out
    elif t == "opponent.delete":
        opp_doc["opponents"] = [x for x in items if x["id"] != o["id"]]
    out[OPPONENTS] = opp_doc
    return out


def _chat_op(t: str, p: dict[str, Any], docs: dict[str, Any], at: str) -> dict[str, Any]:
    path = chat_path(p.get("chat_id"))
    msg_id = p.get("message_id")
    if not isinstance(msg_id, str) or not MSG_RE.fullmatch(msg_id):
        raise OpError("Ungültige Kennung: Frage")
    doc = docs.get(path) if isinstance(docs.get(path), dict) else None
    if t == "chat.cancel":  # take back a question that has no answer yet; an empty conversation goes away
        if doc is None:
            return {}
        msgs = [m for m in doc.get("messages") or [] if isinstance(m, dict)]
        keep = [m for m in msgs if not (m.get("id") == msg_id and not m.get("answer"))]
        if len(keep) == len(msgs):
            return {}
        if not keep:
            return {path: None}
        return {path: {**doc, "messages": keep}}
    question = _text(p.get("question"), 4000)
    if len(question) < 2:
        raise OpError("Die Frage ist zu kurz.")
    doc = dict(doc) if doc else {"id": path.split("/")[-1][:-5], "title": _chat_title(question), "created": at, "updated": at}
    msgs = [m for m in doc.get("messages") or [] if isinstance(m, dict)]
    if any(m.get("id") == msg_id for m in msgs):
        return {}  # sent twice
    msgs.append({"id": msg_id, "asked": at, "question": question, "answer": None, "status": "waiting",
                 "deep": bool(p.get("deep")), "source": "phone"})  # fmt: skip
    doc["messages"], doc["updated"] = msgs, at
    return {path: doc}


def answer_chat(doc: Any, msg_id: str, *, at: str, answer: str = "", cards: dict[str, Any] | None = None,
                decks: dict[str, Any] | None = None, error: str = "") -> dict[str, Any] | None:  # fmt: skip
    """The PC's answer (or why there is none) into the open message ``msg_id``; ``None`` when the question is gone
    (taken back meanwhile) or already answered. Only the sync server writes answers to questions from the phone."""
    if not isinstance(doc, dict):
        return None
    msgs = [dict(m) for m in doc.get("messages") or [] if isinstance(m, dict)]
    m = next((x for x in msgs if x.get("id") == msg_id), None)
    if m is None or m.get("answer"):
        return None
    if answer.strip():
        m.update(answer=answer.strip(), cards=cards or {}, decks=decks or {}, answered=at)
        m.pop("status", None)
        m.pop("error", None)
    else:
        m.update(status="failed", error=_text(error, 300) or "Keine Antwort erhalten.")
    return {**doc, "messages": msgs, "updated": at}


def apply_copy(op: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any]:
    """``apply`` on a deep copy (callers keep their originals)."""
    return apply(op, copy.deepcopy(docs))
