"""Safe file storage for the user's data (decks, collection, opponents, rules …).

- ``atomic_write_text`` writes to a unique temp file next to the target and swaps it in with ``os.replace``,
  so a crash or a parallel reader never sees a half-written file.
- ``locked(path)`` is an exclusive lock across threads *and* processes (the GUI and the MCP server of a
  running Claude job write the same files). It is re-entrant per thread. Hold it only around synchronous
  read-modify-write code – never across an ``await``: do slow work (card lookups) first, then re-read and
  change the data under the lock.
- ``read_json`` returns ``default`` for a missing file. An unreadable file is never treated as empty (the next
  write would wipe the user's data): it is copied to ``<name>.beschaedigt-<timestamp>`` and ``StoreError``
  is raised.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

LOCK_TIMEOUT = 15.0


class StoreError(RuntimeError):
    """A data file is damaged (a copy was kept) or could not be locked."""


class ConflictError(StoreError):
    """The data changed in the meantime (another job or window saved first)."""


_held = threading.local()


def _lock_file(fh: Any) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_file(fh: Any) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked(path: Path, timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Exclusive lock for ``path`` (via ``.<name>.lock`` next to it), re-entrant within a thread."""
    path = Path(path)
    key = str(path.resolve()) if path.parent.exists() else str(path)
    counts: dict[str, int] = getattr(_held, "counts", None) or {}
    _held.counts = counts
    if counts.get(key):
        counts[key] += 1
        try:
            yield
        finally:
            counts[key] -= 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    fh = open(lock_path, "a+b")  # noqa: SIM115 - closed below
    try:
        deadline = time.monotonic() + timeout
        while not _lock_file(fh):
            if time.monotonic() > deadline:
                raise StoreError(f"{path.name} ist gerade gesperrt (anderer Vorgang läuft) – bitte gleich noch einmal versuchen.")
            time.sleep(0.02)
        counts[key] = 1
        try:
            yield
        finally:
            counts[key] = 0
            _unlock_file(fh)
    finally:
        fh.close()


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):  # Windows: the target may be open for a moment by a reader
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def keep_damaged(path: Path) -> Path:
    """Copy a damaged file aside (once per content) and return the copy's path."""
    import hashlib

    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
    backup = path.with_name(f"{path.name}.beschaedigt-{digest}")
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def read_json(path: Path, default: Any = None) -> Any:
    """The parsed file, a copy of ``default`` when it does not exist; ``StoreError`` when it is damaged."""
    path = Path(path)
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        return deepcopy(default)
    try:
        return json.loads(text)
    except ValueError as exc:
        backup = keep_damaged(path)
        raise StoreError(f"Die Datei {path.name} ist beschädigt und wurde nicht überschrieben. Eine Kopie liegt unter "
                         f"{backup.name}; stell sie aus einer Sicherung wieder her (Einstellungen → Daten).") from exc  # fmt: skip


@contextmanager
def update_json(path: Path, default: Any) -> Iterator[Any]:
    """Read-modify-write under the lock: ``with update_json(p, []) as data: data.append(x)`` writes on exit."""
    with locked(path):
        data = read_json(path, default)
        yield data
        write_json(path, data)
