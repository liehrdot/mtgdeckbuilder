"""User-maintained blacklist: cards that must never be put into a deck.

Stored as plain text (one English card name per line, '#' comments allowed) so it can also be
edited by hand. Location: MTG_BLACKLIST_FILE, default <repo>/blacklist.txt (gitignored).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .cards import resolve
from .storage import PROJECT_ROOT

BLACKLIST_FILE = Path(os.environ.get("MTG_BLACKLIST_FILE", PROJECT_ROOT / "blacklist.txt"))

_HEADER = "# Karten, die nie in ein Deck dürfen (eine pro Zeile, englischer Oracle-Name)\n"


def load() -> list[str]:
    try:
        lines = BLACKLIST_FILE.read_text("utf-8").splitlines()
    except FileNotFoundError:
        return []
    return sorted({ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}, key=str.lower)


def names_lower() -> set[str]:
    return {n.lower() for n in load()}


def _write(names: list[str]) -> None:
    BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sorted(set(names), key=str.lower))
    BLACKLIST_FILE.write_text(_HEADER + body + ("\n" if body else ""), "utf-8")


async def update(add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
    """Add/remove cards. Added names are resolved to their English Oracle name (German etc. works)."""
    current = {n.lower(): n for n in load()}
    not_found: list[str] = []
    added: list[str] = []
    if add:
        wanted = [a.strip() for a in add if a.strip()]
        try:
            cards, renames, not_found = await resolve(wanted)
        except Exception:  # offline: keep the names as typed
            cards, renames, not_found = {}, {}, []
            added = wanted
        added += [renames.get(w, w) for w in wanted if w not in not_found and renames.get(w, w) in cards]
        for name in added:
            current[name.lower()] = name
    removed = []
    for name in remove or []:
        if current.pop(name.strip().lower(), None) is not None:
            removed.append(name.strip())
    _write(list(current.values()))
    return {"blacklist": load(), "added": added, "removed": removed, "not_found": not_found}


def filter_cards(cards: list[dict[str, Any]], key: str = "name") -> list[dict[str, Any]]:
    banned = names_lower()
    return [c for c in cards if (c.get(key) or "").lower() not in banned] if banned else cards
