"""The server side of the sync: every document with a revision number, in one SQLite file.

- ``changes(since)`` – documents changed after a sequence number (paged, oldest first);
- ``put(path, base_seq=…)`` – accepted when ``base_seq`` is the server's current number for the path
  (or the path is new), otherwise ``SyncConflict`` with the current document – the client merges and retries;
- deletions are tombstones (``deleted``), so other devices learn about them.

Content travels as canonical bytes (see ``files.canonical``), its sha256 is the ``hash``.
"""

from __future__ import annotations

import base64
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .files import digest, kind_of

PAGE = 200
PAGE_BYTES = 8_000_000


class SyncConflict(Exception):
    """The document changed on the server; ``current`` is its current state."""

    def __init__(self, current: dict[str, Any]):
        super().__init__(f"Konflikt: {current['path']} hat sich auf dem Server geändert")
        self.current = current


def encode(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode("ascii") if data is not None else None


def decode_b64(text: str | None) -> bytes | None:
    return base64.b64decode(text) if text is not None else None


class SyncStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS docs (
                    path TEXT PRIMARY KEY, seq INTEGER NOT NULL, kind TEXT NOT NULL, hash TEXT,
                    data BLOB, deleted INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL, device TEXT);
                CREATE INDEX IF NOT EXISTS docs_seq ON docs(seq);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(row: sqlite3.Row, with_data: bool = True) -> dict[str, Any]:
        doc = {"path": row["path"], "seq": row["seq"], "kind": row["kind"], "hash": row["hash"],
               "deleted": bool(row["deleted"]), "updated": row["updated"], "device": row["device"]}  # fmt: skip
        if with_data:
            doc["data"] = None if row["deleted"] else bytes(row["data"])
        return doc

    def seq(self) -> int:
        with self._db() as db:
            row = db.execute("SELECT MAX(seq) AS s FROM docs").fetchone()
        return int(row["s"] or 0)

    def get(self, path: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM docs WHERE path = ?", (path,)).fetchone()
        return self._row(row) if row else None

    def changes(self, since: int = 0, limit: int | None = None) -> dict[str, Any]:
        """Documents with ``seq > since``, oldest first; ``more`` when the page was cut (count or size)."""
        limit = limit or PAGE
        out, size = [], 0
        with self._db() as db:
            rows = db.execute("SELECT * FROM docs WHERE seq > ? ORDER BY seq LIMIT ?", (since, limit + 1)).fetchall()
        more = len(rows) > limit
        for row in rows[:limit]:
            doc = self._row(row)
            size += len(doc["data"] or b"")
            if out and size > PAGE_BYTES:
                more = True
                break
            out.append(doc)
        cursor = out[-1]["seq"] if out else since
        return {"docs": out, "cursor": cursor, "more": more, "head": self.seq()}

    def put(self, path: str, *, kind: str, data: bytes | None, base_seq: int | None, deleted: bool = False,
            device: str = "") -> dict[str, Any]:  # fmt: skip
        """Store a new state of ``path``; returns ``{"seq", "hash", "unchanged"}``."""
        if kind_of(path) != kind:
            raise ValueError(f"Pfad und Art passen nicht: {path} ({kind})")
        if not deleted and data is None:
            raise ValueError("Inhalt fehlt")
        h = None if deleted else digest(data)  # type: ignore[arg-type]
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM docs WHERE path = ?", (path,)).fetchone()
            if row is not None:
                cur = self._row(row)
                if bool(cur["deleted"]) == deleted and cur["hash"] == h:  # same content: nothing to do
                    return {"seq": cur["seq"], "hash": h, "unchanged": True}
                if base_seq != cur["seq"]:
                    raise SyncConflict(cur)
            seq = (db.execute("SELECT MAX(seq) AS s FROM docs").fetchone()["s"] or 0) + 1
            db.execute(
                "INSERT INTO docs(path, seq, kind, hash, data, deleted, updated, device) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET seq=excluded.seq, kind=excluded.kind, hash=excluded.hash, data=excluded.data, "
                "deleted=excluded.deleted, updated=excluded.updated, device=excluded.device",
                (path, seq, kind, h, None if deleted else data, int(deleted), time.time(), device),
            )
        return {"seq": seq, "hash": h, "unchanged": False}


class LocalTransport:
    """The client talks to a store in the same process (tests; the server's own operations)."""

    def __init__(self, store: SyncStore):
        self.store = store

    def changes(self, since: int) -> dict[str, Any]:
        return self.store.changes(since)

    def put(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.store.put(doc["path"], kind=doc["kind"], data=doc.get("data"), base_seq=doc.get("base_seq"),
                              deleted=bool(doc.get("deleted")), device=doc.get("device", ""))  # fmt: skip
