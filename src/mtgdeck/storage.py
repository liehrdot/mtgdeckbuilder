"""Saved decks live as JSON (+ a plain text export) in the decks/ directory."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deck import DeckEntry, to_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DECKS_DIR = Path(os.environ.get("MTG_DECKS_DIR", PROJECT_ROOT / "decks"))


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "deck"


def save(deck: dict[str, Any]) -> dict[str, str]:
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    deck_slug = deck.get("slug") or slug(deck["name"])
    deck["slug"] = deck_slug
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    json_path = DECKS_DIR / f"{deck_slug}.json"
    if json_path.exists():
        try:
            deck.setdefault("created", json.loads(json_path.read_text("utf-8")).get("created", now))
        except ValueError:
            pass
    deck.setdefault("created", now)
    deck["updated"] = now
    json_path.write_text(json.dumps(deck, indent=2, ensure_ascii=False), "utf-8")

    entries = [DeckEntry(c["name"], int(c.get("qty", 1))) for c in deck.get("cards", [])]
    txt_path = DECKS_DIR / f"{deck_slug}.txt"
    txt_path.write_text(to_text(deck.get("commanders", []), entries), "utf-8")
    return {"json": str(json_path), "text": str(txt_path), "slug": deck_slug}


def load(deck_slug: str) -> dict[str, Any]:
    path = DECKS_DIR / f"{slug(deck_slug)}.json"
    if not path.exists():
        raise FileNotFoundError(f"No saved deck '{deck_slug}' in {DECKS_DIR}")
    return json.loads(path.read_text("utf-8"))


def list_decks() -> list[dict[str, Any]]:
    if not DECKS_DIR.exists():
        return []
    out = []
    for path in sorted(DECKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(path.read_text("utf-8"))
        except ValueError:
            continue
        out.append(
            {
                "slug": d.get("slug", path.stem),
                "name": d.get("name", path.stem),
                "commanders": d.get("commanders", []),
                "bracket": d.get("bracket"),
                "proxy": bool(d.get("proxy")),
                "updated": d.get("updated"),
                "valid": (d.get("validation") or {}).get("legal"),
            }
        )
    return out
