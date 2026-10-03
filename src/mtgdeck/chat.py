"""Conversations with the whole app („Frag Claude“): one file per conversation in ``decks/.chats/<id>.json``.

A conversation is ``{id, title, created, updated, messages: [{id, asked, question, answer, cards, decks}]}`` –
one entry per question and answer. Lives under ``decks/`` so backups include it.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import storage
from .jsonstore import locked, read_json, update_json

DIRNAME = ".chats"
TITLE_LEN = 70
_ID = re.compile(r"^[0-9a-f]{12}$")


def _dir() -> Path:
    return storage.DECKS_DIR / DIRNAME


def _file(chat_id: str) -> Path:
    if not _ID.match(chat_id or ""):
        raise FileNotFoundError(f"Kein Gespräch „{chat_id}“")
    return _dir() / f"{chat_id}.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _title(question: str) -> str:
    t = " ".join(question.split())
    return t if len(t) <= TITLE_LEN else t[: TITLE_LEN - 2].rstrip() + " …"


def get(chat_id: str) -> dict[str, Any]:
    data = read_json(_file(chat_id))
    if not isinstance(data, dict):
        raise FileNotFoundError(f"Kein Gespräch „{chat_id}“")
    return data


def chats() -> list[dict[str, Any]]:
    """All conversations, last active first: id, title, updated, count (without the messages)."""
    out = []
    for p in _dir().glob("*.json") if _dir().exists() else []:
        if not _ID.match(p.stem):
            continue
        try:
            c = read_json(p, {})
        except Exception:  # a damaged file is kept aside by read_json; just skip it in the list
            continue
        out.append({"id": c.get("id", p.stem), "title": c.get("title", ""), "created": c.get("created"),
                    "updated": c.get("updated"), "count": len(c.get("messages") or [])})  # fmt: skip
    return sorted(out, key=lambda c: c["updated"] or "", reverse=True)


def create(question: str) -> dict[str, Any]:
    chat_id = uuid.uuid4().hex[:12]
    data = {"id": chat_id, "title": _title(question), "created": _now(), "updated": _now(), "messages": []}
    with update_json(_file(chat_id), {}) as c:
        c.update(data)
    return data


def add(chat_id: str, question: str, answer: str, **extra: Any) -> dict[str, Any]:
    """Append one question and its answer; returns the new entry."""
    entry = {"id": uuid.uuid4().hex[:10], "asked": _now(), "question": question, "answer": answer, **extra}
    path = _file(chat_id)
    with update_json(path, {}) as c:
        if not c:
            raise FileNotFoundError(f"Kein Gespräch „{chat_id}“")
        c.setdefault("messages", []).append(entry)
        c["updated"] = entry["asked"]
    return entry


def rename(chat_id: str, title: str) -> dict[str, Any]:
    with update_json(_file(chat_id), {}) as c:
        if not c:
            raise FileNotFoundError(f"Kein Gespräch „{chat_id}“")
        c["title"] = _title(title) or c.get("title", "")
    return c


def delete(chat_id: str) -> None:
    path = _file(chat_id)
    with locked(path):
        if not path.exists():
            raise FileNotFoundError(f"Kein Gespräch „{chat_id}“")
        path.unlink()
