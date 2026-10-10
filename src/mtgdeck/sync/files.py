"""Which files sync, under which logical path, and how they are read, hashed and written.

A logical path is the same name the zip backup uses (``decks/meren.json``, ``collection.json``,
``proxies/meren/selection.json`` …), so every device maps it to its own data folders (``Roots``).

Kinds:
- ``deck``     – ``decks/<slug>.json`` (own merge rule, writes the ``.txt`` export too)
- ``snapshot`` – ``decks/.versions/<slug>/vNNNN.json`` (immutable, renumbered on collisions)
- ``json``     – everything else that is JSON (generic three-way merge)
- ``lines``    – ``blacklist.txt`` (lines as a set)
- ``blob``     – images (the version on the server wins)

Not synced: card DB, caches, print files, renders, ``.txt`` exports, trash, backups, settings, lock/temp files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..jsonstore import atomic_write_text, locked, write_json

RULES: list[tuple[re.Pattern[str], str]] = [(re.compile(p), k) for p, k in [
    (r"decks/[^/.][^/]*\.json", "deck"),
    (r"decks/\.versions/[^/.][^/]*/v\d{4,}\.json", "snapshot"),
    (r"decks/\.(games|questions)/[^/.][^/]*\.json", "json"),
    (r"decks/\.chats/[0-9a-f]{12}\.json", "json"),
    (r"decks/\.(opponents|meta-suggestions)\.json", "json"),
    (r"collection\.json|tablerules\.json", "json"),
    (r"blacklist\.txt", "lines"),
    (r"proxies/\.orders/[^/.][^/]*\.json", "json"),
    (r"proxies/[^/.][^/]*/(selection|tokens)\.json", "json"),
    (r"proxies/[^/.][^/]*/uploads/index\.json", "json"),
    (r"proxies/[^/.][^/]*/uploads/[0-9a-f]{16}(-original)?\.(jpg|png|webp|tif|bmp|gif)", "blob"),
    (r"deskmats/[^/.][^/]*/meta\.json", "json"),
    (r"deskmats/[^/.][^/]*/source\.[a-z0-9]{2,5}", "blob"),
]]  # fmt: skip
SNAPSHOT_RE = re.compile(r"decks/\.versions/([^/]+)/v(\d+)\.json")
DECK_RE = re.compile(r"decks/([^/.][^/]*)\.json")


def kind_of(path: str) -> str | None:
    """The kind of a logical path, ``None`` when it does not sync."""
    if ".." in path.split("/") or path.startswith("/"):
        return None
    return next((k for rx, k in RULES if rx.fullmatch(path)), None)


def snapshot_path(deck_slug: str, version: int) -> str:
    return f"decks/.versions/{deck_slug}/v{int(version):04d}.json"


@dataclass(frozen=True)
class Roots:
    """Where the logical paths live on this device (plus the folder for sync state)."""

    decks: Path
    collection: Path
    blacklist: Path
    tablerules: Path
    proxies: Path
    deskmats: Path
    state: Path

    @classmethod
    def current(cls) -> Roots:
        """The app's configured folders (read at call time, so tests and env overrides apply)."""
        from .. import blacklist, collection, deskmat, proxy, storage, tablerules

        state = Path(os.environ.get("MTG_SYNC_DIR", storage.PROJECT_ROOT / ".sync"))
        return cls(storage.DECKS_DIR, collection.COLLECTION_FILE, blacklist.BLACKLIST_FILE, tablerules.TABLERULES_FILE,
                   proxy.PROXIES_DIR, deskmat.DESKMAT_DIR, state)  # fmt: skip

    @classmethod
    def under(cls, base: Path) -> Roots:
        """Everything below one folder (tests, a second device)."""
        return cls(base / "decks", base / "collection.json", base / "blacklist.txt", base / "tablerules.json",
                   base / "proxies", base / "deskmats", base / ".sync")  # fmt: skip

    def physical(self, path: str) -> Path:
        head, _, rest = path.partition("/")
        single = {"collection.json": self.collection, "blacklist.txt": self.blacklist, "tablerules.json": self.tablerules}
        if path in single:
            return single[path]
        base = {"decks": self.decks, "proxies": self.proxies, "deskmats": self.deskmats}.get(head)
        if base is None or not rest:
            raise ValueError(f"Kein Sync-Pfad: {path}")
        return base.joinpath(*rest.split("/"))

    def scan(self) -> dict[str, Path]:
        """All local files that sync: logical path -> file."""
        out: dict[str, Path] = {}
        for name, p in (("collection.json", self.collection), ("blacklist.txt", self.blacklist), ("tablerules.json", self.tablerules)):
            if p.is_file():
                out[name] = p
        for head, base in (("decks", self.decks), ("proxies", self.proxies), ("deskmats", self.deskmats)):
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if p.is_file():
                    logical = f"{head}/{p.relative_to(base).as_posix()}"
                    if kind_of(logical):
                        out[logical] = p
        return out


# --- content ------------------------------------------------------------------------------------


def canonical(kind: str, value: Any) -> bytes:
    """Stable bytes for hashing and transport: sorted compact JSON, normalised text, raw bytes."""
    if kind in ("deck", "snapshot", "json"):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if kind == "lines":
        return (value if value.endswith("\n") or not value else value + "\n").encode("utf-8")
    return bytes(value)


def decode(kind: str, data: bytes) -> Any:
    if kind in ("deck", "snapshot", "json"):
        return json.loads(data.decode("utf-8"))
    if kind == "lines":
        return data.decode("utf-8")
    return data


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_local(kind: str, path: Path) -> Any:
    """The parsed local file (``ValueError`` for damaged JSON)."""
    if kind in ("deck", "snapshot", "json"):
        return json.loads(path.read_text("utf-8"))
    if kind == "lines":
        return path.read_text("utf-8").replace("\r\n", "\n")
    return path.read_bytes()


def local_hash(kind: str, path: Path) -> str:
    return digest(canonical(kind, read_local(kind, path)))


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_local(roots: Roots, path: str, kind: str, value: Any) -> None:
    """Write a value to its local file (decks also get their ``.txt`` export)."""
    target = roots.physical(path)
    if kind in ("deck", "snapshot", "json"):
        write_json(target, value)
        if kind == "deck":
            from ..deck import DeckEntry, to_text

            entries = [DeckEntry(c["name"], int(c.get("qty", 1))) for c in value.get("cards", [])]
            atomic_write_text(target.with_suffix(".txt"), to_text(value.get("commanders", []), entries))
    elif kind == "lines":
        atomic_write_text(target, value)
    else:
        _write_bytes(target, value)


def delete_local(roots: Roots, path: str, kind: str) -> None:
    """Remove a local file; a deck goes to the trash (``decks/.trash/<ts>-<slug>-sync/``) instead."""
    target = roots.physical(path)
    if not target.exists():
        return
    if kind == "deck":
        trash = roots.decks / ".trash" / f"{time.strftime('%Y%m%d-%H%M%S')}-{target.stem}-sync"
        trash.mkdir(parents=True, exist_ok=True)
        os.replace(target, trash / target.name)
        txt = target.with_suffix(".txt")
        if txt.exists():
            os.replace(txt, trash / txt.name)
    else:
        target.unlink(missing_ok=True)


def guarded(roots: Roots, path: str):
    """The lock the app itself uses for this file – the sync never writes without it."""
    return locked(roots.physical(path))
