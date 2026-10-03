"""Saved decks live as JSON (+ a plain text export) in the decks/ directory, with a full
snapshot per version in decks/.versions/<slug>/ (history, diffs, restore, copy) and the questions
asked about a deck in decks/.questions/<slug>.json. Deleted decks go to decks/.trash/ and can be
restored.

All writes are atomic and locked (``jsonstore``): the GUI and the MCP server of a running Claude job
save the same decks."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deck import DeckEntry, to_text
from .jsonstore import ConflictError, StoreError, atomic_write_text, locked, read_json, update_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DECKS_DIR = Path(os.environ.get("MTG_DECKS_DIR", PROJECT_ROOT / "decks"))


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "deck"


VERSIONS_DIRNAME = ".versions"  # decks/.versions/<slug>/v0001.json – full snapshot per version

# Fields whose change creates a new version (re-validation alone does not).
_CONTENT_KEYS = ("name", "commanders", "cards", "bracket", "power_profile", "proxy", "budget", "currency",
                 "description", "strategy", "notes", "table_rule")  # fmt: skip
# Written on every save; everything else that is not content (guide, upgrade_plan, built_against, precon,
# copied_from, last_job …) is carried over when a caller saves a deck dict built from scratch.
_PER_SAVE_KEYS = {"slug", "history", "version", "created", "updated", "validation", "change_note"}
TRASH_DIRNAME = ".trash"


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
    write_json(d / f"v{deck['version']:04d}.json", snap)


def _path(deck_slug: str) -> Path:
    return DECKS_DIR / f"{slug(deck_slug)}.json"


def _write_current(deck: dict[str, Any]) -> dict[str, str]:
    json_path = DECKS_DIR / f"{deck['slug']}.json"
    write_json(json_path, deck)
    entries = [DeckEntry(c["name"], int(c.get("qty", 1))) for c in deck.get("cards", [])]
    txt_path = DECKS_DIR / f"{deck['slug']}.txt"
    atomic_write_text(txt_path, to_text(deck.get("commanders", []), entries))
    return {"json": str(json_path), "text": str(txt_path), "slug": deck["slug"], "version": deck.get("version")}


def save(deck: dict[str, Any], *, expect_version: int | None = None) -> dict[str, Any]:
    """Write the deck as its current version.

    Every content change (cards, bracket, profile, texts ...) or an explicit ``change_note``
    creates a new version: a full snapshot in decks/.versions/<slug>/ plus a history entry with
    the card diff, level (e.g. 'oberes Bracket 3'), price and power score. Pure re-validation
    only updates the current file. Non-content fields of the saved deck (guide, upgrade plan …)
    are kept when ``deck`` lacks them.

    ``expect_version``: the version the caller started from; ``ConflictError`` if someone saved a
    newer one meanwhile (re-load and apply the change again).
    """
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    deck_slug = deck.get("slug") or slug(deck["name"])
    deck["slug"] = deck_slug
    with locked(_path(deck_slug)):
        return _save_locked(deck, deck_slug, expect_version)


def _save_locked(deck: dict[str, Any], deck_slug: str, expect_version: int | None) -> dict[str, Any]:
    now = _now()
    note = deck.pop("change_note", "") or ""
    old: dict[str, Any] | None = read_json(_path(deck_slug))
    if expect_version is not None and (old or {}).get("version") != expect_version:
        deck["change_note"] = note
        raise ConflictError(f"Das Deck „{deck.get('name', deck_slug)}“ wurde inzwischen geändert – bitte neu laden.")
    for key, value in (old or {}).items():
        if key not in deck and key not in _CONTENT_KEYS and key not in _PER_SAVE_KEYS:
            deck[key] = value
    if os.environ.get("MTG_JOB_ID"):  # saved by the MCP server of a GUI job: lets the GUI find "its" deck
        deck["last_job"] = os.environ["MTG_JOB_ID"]

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
    with locked(_path(deck_slug)):
        deck = load(deck_slug)
        if value is None:
            deck.pop(key, None)
        else:
            deck[key] = value
        _write_current(deck)
    return deck


def load(deck_slug: str) -> dict[str, Any]:
    deck = read_json(_path(deck_slug))
    if deck is None:
        raise FileNotFoundError(f"No saved deck '{deck_slug}' in {DECKS_DIR}")
    return deck


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
    snap = read_json(_versions_dir(deck_slug) / f"v{int(version):04d}.json")
    if snap is None:
        raise FileNotFoundError(f"Deck '{deck_slug}' has no version {version}")
    return snap


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


def _deck_files(s: str) -> dict[str, Path]:
    """Everything that belongs to a deck, by its name inside a trash entry."""
    return {"deck.json": DECKS_DIR / f"{s}.json", "deck.txt": DECKS_DIR / f"{s}.txt",
            "questions.json": _questions_file(s), "games.json": DECKS_DIR / ".games" / f"{s}.json",  # games.GAMES_DIRNAME
            "versions": _versions_dir(s)}  # fmt: skip


def delete(deck_slug: str) -> str:
    """Move a deck with its versions, questions and games to the trash; returns the trash id."""
    s = slug(deck_slug)
    with locked(_path(s)):
        deck = load(s)
        trash_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{s}"
        target = DECKS_DIR / TRASH_DIRNAME / trash_id
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / "info.json", {"slug": s, "name": deck.get("name") or s, "commanders": deck.get("commanders", []),
                                          "deleted": _now(), "version": deck.get("version")})  # fmt: skip
        for name, src in _deck_files(s).items():
            if src.exists():
                shutil.move(str(src), str(target / name))
    return trash_id


def trash() -> list[dict[str, Any]]:
    """Deleted decks, newest first."""
    base = DECKS_DIR / TRASH_DIRNAME
    out = []
    for d in sorted(base.iterdir(), reverse=True) if base.exists() else []:
        try:
            info = read_json(d / "info.json")
        except StoreError:
            info = None
        if info:
            out.append({**info, "id": d.name})
    return out


def _trash_dir(trash_id: str) -> Path:
    if not re.fullmatch(r"\d{8}-\d{6}-[a-z0-9-]+", trash_id):
        raise FileNotFoundError(f"Nicht im Papierkorb: {trash_id}")
    d = DECKS_DIR / TRASH_DIRNAME / trash_id
    if not (d / "info.json").exists():
        raise FileNotFoundError(f"Nicht im Papierkorb: {trash_id}")
    return d


def restore_deleted(trash_id: str) -> dict[str, Any]:
    """Bring a deleted deck back (under a new slug if the old one is taken again)."""
    d = _trash_dir(trash_id)
    info = read_json(d / "info.json")
    s = unique_slug(info["slug"]) if _path(info["slug"]).exists() else info["slug"]
    with locked(_path(s)):
        deck = read_json(d / "deck.json")
        deck["slug"] = s
        for name, dst in _deck_files(s).items():
            src = d / name
            if src.exists() and name != "deck.json":
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        _write_current(deck)
    shutil.rmtree(d, ignore_errors=True)
    return {"slug": s, "name": deck.get("name")}


def purge_deleted(trash_id: str | None = None) -> int:
    """Finally delete one trash entry, or all of them; returns how many."""
    targets = [_trash_dir(trash_id)] if trash_id else [DECKS_DIR / TRASH_DIRNAME / t["id"] for t in trash()]
    for d in targets:
        shutil.rmtree(d, ignore_errors=True)
    return len(targets)


# --- questions about a deck ("Fragen zum Deck") ---------------------------------------------------

QUESTIONS_DIRNAME = ".questions"  # decks/.questions/<slug>.json – question/answer log per deck


def _questions_file(deck_slug: str) -> Path:
    return DECKS_DIR / QUESTIONS_DIRNAME / f"{slug(deck_slug)}.json"


def questions(deck_slug: str) -> list[dict[str, Any]]:
    """Questions asked about a deck, oldest first: id, asked, question, answer, version."""
    items = read_json(_questions_file(deck_slug), [])
    return items if isinstance(items, list) else []


def add_question(deck_slug: str, question: str, answer: str, **extra: Any) -> dict[str, Any]:
    entry = {"id": uuid.uuid4().hex[:10], "asked": _now(), "question": question, "answer": answer, **extra}
    with update_json(_questions_file(deck_slug), []) as items:
        items.append(entry)
    return entry


def delete_questions(deck_slug: str, entry_id: str | None = None) -> int:
    """Delete one question (``entry_id``) or all of them; returns how many were removed."""
    path = _questions_file(deck_slug)
    with locked(path):
        items = questions(deck_slug)
        keep = [q for q in items if entry_id is not None and q.get("id") != entry_id]
        if keep:
            write_json(path, keep)
        else:
            path.unlink(missing_ok=True)
    return len(items) - len(keep)


def list_decks() -> list[dict[str, Any]]:
    if not DECKS_DIR.exists():
        return []
    out = []
    paths = [p for p in DECKS_DIR.glob("*.json") if not p.name.startswith(".")]  # .opponents.json etc. are no decks
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(path.read_text("utf-8"))
        except (ValueError, OSError):  # damaged: listed so it does not silently vanish; opening it keeps a copy
            out.append({"slug": path.stem, "name": f"{path.stem} (beschädigt)", "commanders": [], "damaged": True,
                        "level": "", "updated": None, "version": None, "valid": False})  # fmt: skip
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
                "table_rule": d.get("table_rule"),
                "last_job": d.get("last_job"),
            }
        )
    return out
