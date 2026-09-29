"""Saved decks live as JSON (+ a plain text export) in the decks/ directory, with a full
snapshot per version in decks/.versions/<slug>/ (history, diffs, restore, copy) and the questions
asked about a deck in decks/.questions/<slug>.json."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deck import DeckEntry, to_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DECKS_DIR = Path(os.environ.get("MTG_DECKS_DIR", PROJECT_ROOT / "decks"))


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "deck"


VERSIONS_DIRNAME = ".versions"  # decks/.versions/<slug>/v0001.json – full snapshot per version

# Fields whose change creates a new version (re-validation alone does not).
_CONTENT_KEYS = ("name", "commanders", "cards", "bracket", "power_profile", "proxy", "budget", "currency",
                 "description", "strategy", "notes")  # fmt: skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _versions_dir(deck_slug: str) -> Path:
    return DECKS_DIR / VERSIONS_DIRNAME / slug(deck_slug)


def _card_counts(deck: dict[str, Any]) -> dict[str, int]:
    counts = {c["name"]: int(c.get("qty", 1)) for c in deck.get("cards", [])}
    for cmdr in deck.get("commanders", []):
        counts[f"{cmdr} (Commander)"] = 1
    return counts


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    """Cards added/removed between two deck versions ('2 Forest' for count changes)."""
    a, b = _card_counts(old), _card_counts(new)
    added = [f"{b[n] - a.get(n, 0)} {n}" if b[n] - a.get(n, 0) > 1 else n for n in sorted(b) if b[n] > a.get(n, 0)]
    removed = [f"{a[n] - b.get(n, 0)} {n}" if a[n] - b.get(n, 0) > 1 else n for n in sorted(a) if a[n] > b.get(n, 0)]
    return {"added": added, "removed": removed}


def level_text(deck: dict[str, Any]) -> str:
    tier = (deck.get("power_profile") or {}).get("tier")
    labels = {"low": "unteres", "mid": "mittleres", "high": "oberes"}
    return f"{labels[tier]} Bracket {deck.get('bracket')}" if tier else f"Bracket {deck.get('bracket')}"


def _metrics(deck: dict[str, Any]) -> dict[str, Any]:
    v = deck.get("validation") or {}
    return {
        "level": level_text(deck),
        "price": v.get("price_total"),
        "power": ((v.get("bracket") or {}).get("power") or {}).get("value"),
        "legal": v.get("legal"),
    }


def _content(deck: dict[str, Any]) -> str:
    return json.dumps({k: deck.get(k) for k in _CONTENT_KEYS}, sort_keys=True, default=str)


def _snapshot(deck: dict[str, Any]) -> None:
    d = _versions_dir(deck["slug"])
    d.mkdir(parents=True, exist_ok=True)
    snap = {k: v for k, v in deck.items() if k != "history"}
    (d / f"v{deck['version']:04d}.json").write_text(json.dumps(snap, indent=2, ensure_ascii=False), "utf-8")


def _write_current(deck: dict[str, Any]) -> dict[str, str]:
    json_path = DECKS_DIR / f"{deck['slug']}.json"
    json_path.write_text(json.dumps(deck, indent=2, ensure_ascii=False), "utf-8")
    entries = [DeckEntry(c["name"], int(c.get("qty", 1))) for c in deck.get("cards", [])]
    txt_path = DECKS_DIR / f"{deck['slug']}.txt"
    txt_path.write_text(to_text(deck.get("commanders", []), entries), "utf-8")
    return {"json": str(json_path), "text": str(txt_path), "slug": deck["slug"], "version": deck.get("version")}


def save(deck: dict[str, Any]) -> dict[str, Any]:
    """Write the deck as its current version.

    Every content change (cards, bracket, profile, texts ...) or an explicit ``change_note``
    creates a new version: a full snapshot in decks/.versions/<slug>/ plus a history entry with
    the card diff, level (e.g. 'oberes Bracket 3'), price and power score. Pure re-validation
    only updates the current file.
    """
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    deck_slug = deck.get("slug") or slug(deck["name"])
    deck["slug"] = deck_slug
    now = _now()
    note = deck.pop("change_note", "") or ""
    json_path = DECKS_DIR / f"{deck_slug}.json"
    old: dict[str, Any] | None = None
    if json_path.exists():
        try:
            old = json.loads(json_path.read_text("utf-8"))
        except ValueError:
            old = None

    if old and not old.get("version"):  # deck from before versioning: keep it as version 1
        old["version"] = 1
        old.setdefault("history", []).append(
            {"version": 1, "at": old.get("updated") or now, "note": "Ursprüngliche Version", "added": [], "removed": [],
             "from": None, "to": level_text(old), **_metrics(old)}  # fmt: skip
        )
        _snapshot(old)

    deck["history"] = list((old or {}).get("history") or [])
    deck["created"] = (old or {}).get("created") or deck.get("created") or now
    deck["updated"] = now
    if old is None or note or _content(old) != _content(deck):
        deck["version"] = (old or {}).get("version", 0) + 1
        change = diff(old, deck) if old else {"added": [], "removed": []}
        deck["history"].append(
            {
                "version": deck["version"],
                "at": now,
                "note": note or ("Erstellt" if old is None else ""),
                **change,
                "from": level_text(old) if old else None,
                "to": level_text(deck),
                **_metrics(deck),
            }
        )
        _snapshot(deck)
    else:
        deck["version"] = old["version"]
        if deck["history"]:  # refresh metrics of the current version (e.g. after re-validation)
            deck["history"][-1].update(_metrics(deck))
    return _write_current(deck)


def set_extra(deck_slug: str, key: str, value: Any) -> dict[str, Any]:
    """Store data that belongs to the deck but is not deck content (e.g. the play guide) in the
    current file – no new version, ``updated`` stays as it is."""
    if key in _CONTENT_KEYS or key in ("slug", "version", "history", "cards"):
        raise ValueError(f"{key} ist Deck-Inhalt")
    deck = load(deck_slug)
    if value is None:
        deck.pop(key, None)
    else:
        deck[key] = value
    _write_current(deck)
    return deck


def load(deck_slug: str) -> dict[str, Any]:
    path = DECKS_DIR / f"{slug(deck_slug)}.json"
    if not path.exists():
        raise FileNotFoundError(f"No saved deck '{deck_slug}' in {DECKS_DIR}")
    return json.loads(path.read_text("utf-8"))


# --- versions -------------------------------------------------------------------------------


def versions(deck_slug: str) -> list[dict[str, Any]]:
    """History entries (newest last), each marked whether a restorable snapshot exists."""
    deck = load(deck_slug)
    vdir = _versions_dir(deck_slug)
    out = []
    for h in deck.get("history") or []:
        v = h.get("version")
        out.append({**h, "restorable": bool(v) and (vdir / f"v{v:04d}.json").exists(), "current": v == deck.get("version")})
    return out


def load_version(deck_slug: str, version: int | None = None) -> dict[str, Any]:
    """A full deck snapshot; ``None`` = current version."""
    if version is None:
        return load(deck_slug)
    path = _versions_dir(deck_slug) / f"v{int(version):04d}.json"
    if not path.exists():
        raise FileNotFoundError(f"Deck '{deck_slug}' has no version {version}")
    return json.loads(path.read_text("utf-8"))


def compare(deck_slug: str, a: int, b: int | None = None) -> dict[str, Any]:
    """Diff version ``a`` -> version ``b`` (default: current)."""
    old, new = load_version(deck_slug, a), load_version(deck_slug, b)
    mo, mn = _metrics(old), _metrics(new)
    return {
        "from_version": old.get("version", a),
        "to_version": new.get("version", b),
        **diff(old, new),
        "level": {"from": mo["level"], "to": mn["level"]},
        "price": {"from": mo["price"], "to": mn["price"]},
        "power": {"from": mo["power"], "to": mn["power"]},
        "commanders_changed": old.get("commanders") != new.get("commanders"),
    }


def restore(deck_slug: str, version: int, note: str = "") -> dict[str, Any]:
    """Make an old version current again. Saved as a *new* version – nothing is lost."""
    snap = load_version(deck_slug, version)
    current = load(deck_slug)
    snap.pop("history", None)
    snap["slug"] = current["slug"]
    snap["change_note"] = f"Version {version} wiederhergestellt" + (f": {note}" if note else "")
    return save(snap)


def unique_slug(name: str) -> str:
    base = slug(name)
    candidate, n = base, 2
    while (DECKS_DIR / f"{candidate}.json").exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def copy(deck_slug: str, new_name: str | None = None, version: int | None = None) -> dict[str, Any]:
    """Duplicate a deck (optionally from an old version) as a new deck with its own history."""
    src = load_version(deck_slug, version)
    name = new_name or f"{src.get('name', deck_slug)} (Kopie)"
    new = {k: v for k, v in src.items() if k not in {"history", "version", "created", "updated", "slug"}}
    new["name"] = name
    new["slug"] = unique_slug(name)
    new["copied_from"] = {"slug": slug(deck_slug), "version": src.get("version")}
    src_v = f" (Version {src.get('version')})" if src.get("version") else ""
    new["change_note"] = f"Kopie von „{src.get('name', deck_slug)}“{src_v}"
    return save(new)


def delete(deck_slug: str) -> None:
    s = slug(deck_slug)
    for ext in ("json", "txt"):
        (DECKS_DIR / f"{s}.{ext}").unlink(missing_ok=True)
    _questions_file(s).unlink(missing_ok=True)
    (DECKS_DIR / ".games" / f"{s}.json").unlink(missing_ok=True)  # games.GAMES_DIRNAME
    vdir = _versions_dir(s)
    if vdir.exists():
        for f in vdir.glob("*.json"):
            f.unlink()
        vdir.rmdir()


# --- questions about a deck ("Fragen zum Deck") ---------------------------------------------------

QUESTIONS_DIRNAME = ".questions"  # decks/.questions/<slug>.json – question/answer log per deck


def _questions_file(deck_slug: str) -> Path:
    return DECKS_DIR / QUESTIONS_DIRNAME / f"{slug(deck_slug)}.json"


def questions(deck_slug: str) -> list[dict[str, Any]]:
    """Questions asked about a deck, oldest first: id, asked, question, answer, version."""
    path = _questions_file(deck_slug)
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return items if isinstance(items, list) else []


def add_question(deck_slug: str, question: str, answer: str, **extra: Any) -> dict[str, Any]:
    entry = {"id": uuid.uuid4().hex[:10], "asked": _now(), "question": question, "answer": answer, **extra}
    path = _questions_file(deck_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(questions(deck_slug) + [entry], ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def delete_questions(deck_slug: str, entry_id: str | None = None) -> int:
    """Delete one question (``entry_id``) or all of them; returns how many were removed."""
    items = questions(deck_slug)
    keep = [q for q in items if entry_id is not None and q.get("id") != entry_id]
    path = _questions_file(deck_slug)
    if keep:
        path.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return len(items) - len(keep)


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
                "tier": (d.get("power_profile") or {}).get("tier"),
                "level": level_text(d),
                "proxy": bool(d.get("proxy")),
                "updated": d.get("updated"),
                "version": d.get("version"),
                "valid": (d.get("validation") or {}).get("legal"),
            }
        )
    return out
