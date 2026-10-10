"""Backups of all the user's data as one zip file – export, import and automatic daily copies.

A backup holds everything that cannot be re-downloaded:

- ``decks/`` – decks with versions, games, conversations, opponent decks, meta suggestions and the trash;
- ``collection.json``, ``blacklist.txt``, ``tablerules.json``, ``mtgdeck.settings.json``;
- ``proxies/.orders/`` (collective print orders), ``proxies/<deck>/selection.json`` (chosen card images) and
  ``proxies/<deck>/tokens.json`` (copies per token), ``proxies/<deck>/uploads/`` (own card images);

Backups live in ``backups/`` (``MTG_BACKUP_DIR``, gitignored): ``auto-*`` once a day when the GUI starts
(the last ``KEEP_AUTO`` are kept), ``manuell-*`` on request, ``vor-wiederherstellung-*`` right before a
restore, ``hochgeladen-*`` for imported files. Restoring replaces the current data with the backup's.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import storage

BACKUP_DIR = Path(os.environ.get("MTG_BACKUP_DIR", storage.PROJECT_ROOT / "backups"))
KEEP_AUTO = 10
PROXY_FILES = ("selection.json", "tokens.json")  # per deck: chosen images, token quantities
FORMAT = 1
KINDS = {"auto": "automatisch", "manuell": "von dir angelegt", "vor-wiederherstellung": "vor einer Wiederherstellung",
         "hochgeladen": "hochgeladen"}  # fmt: skip
_NAME_RE = re.compile(r"^(auto|manuell|vor-wiederherstellung|hochgeladen)-\d{8}-\d{6}(-\d+)?\.zip$")
_SKIP = re.compile(r"(^|/)\.[^/]+\.(lock|tmp)$|\.tmp$|\.lock$")


def _files() -> dict[str, Path]:
    """Single files: name inside the zip -> path on disk (read at call time, so tests/env can redirect)."""
    from . import blacklist, collection, settings, tablerules

    return {"collection.json": collection.COLLECTION_FILE, "blacklist.txt": blacklist.BLACKLIST_FILE,
            "tablerules.json": tablerules.TABLERULES_FILE, "mtgdeck.settings.json": settings.SETTINGS_FILE}  # fmt: skip


def _members() -> list[tuple[str, Path]]:
    from . import proxy

    out: list[tuple[str, Path]] = []
    if storage.DECKS_DIR.exists():
        for p in sorted(storage.DECKS_DIR.rglob("*")):
            rel = p.relative_to(storage.DECKS_DIR).as_posix()
            if p.is_file() and not _SKIP.search(rel):
                out.append((f"decks/{rel}", p))
    for name, p in _files().items():
        if p.exists():
            out.append((name, p))
    orders = proxy.PROXIES_DIR / ".orders"
    for p in sorted(orders.glob("*.json")) if orders.exists() else []:
        out.append((f"proxies/.orders/{p.name}", p))
    for name in PROXY_FILES:
        for p in sorted(proxy.PROXIES_DIR.glob(f"*/{name}")) if proxy.PROXIES_DIR.exists() else []:
            out.append((f"proxies/{p.parent.name}/{name}", p))
    for p in sorted(proxy.PROXIES_DIR.glob("*/uploads/*")) if proxy.PROXIES_DIR.exists() else []:
        if p.is_file() and not _SKIP.search(p.name):  # own card images
            out.append((f"proxies/{p.parent.parent.name}/uploads/{p.name}", p))
    return out


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _target(kind: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    same = [_age_key(p)[1] for p in BACKUP_DIR.glob(f"{kind}-{stamp}*.zip")]
    n = max(same, default=0) + 1  # always count upwards within one second, even after pruning
    return BACKUP_DIR / (f"{kind}-{stamp}.zip" if n == 1 else f"{kind}-{stamp}-{n}.zip")


def create(kind: str = "manuell") -> dict[str, Any]:
    """Write a backup zip and return its info."""
    if kind not in KINDS:
        raise ValueError(f"Unbekannte Art: {kind}")
    members = _members()
    decks = [m for m, _ in members if re.fullmatch(r"decks/[^/.][^/]*\.json", m)]
    manifest = {"format": FORMAT, "app": "mtgdeckbuilder", "created": storage._now(), "kind": kind,
                "decks": len(decks), "files": len(members)}  # fmt: skip
    path = _target(kind)
    tmp = path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("backup.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, p in members:
            z.write(p, name)
    os.replace(tmp, path)
    if kind == "auto":
        _prune()
    return info(path)


def _age_key(p: Path) -> tuple[str, int]:
    """Newest last: the timestamp in the name, then the counter of backups made in the same second."""
    m = re.match(r"^[a-z-]+-(\d{8}-\d{6})(?:-(\d+))?\.zip$", p.name)
    return (m.group(1), int(m.group(2) or 1)) if m else ("", 0)


def _prune() -> None:
    autos = sorted(BACKUP_DIR.glob("auto-*.zip"), key=_age_key, reverse=True)
    for old in autos[KEEP_AUTO:]:
        old.unlink(missing_ok=True)


def info(path: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("backup.json"))
    except (KeyError, ValueError, zipfile.BadZipFile, OSError):
        pass
    kind = next((k for k in sorted(KINDS, key=len, reverse=True) if path.name.startswith(k + "-")), "")
    return {"name": path.name, "kind": kind, "kind_label": KINDS.get(kind, kind), "size": path.stat().st_size,
            "created": manifest.get("created"), "decks": manifest.get("decks"), "files": manifest.get("files"),
            "valid": manifest.get("app") == "mtgdeckbuilder"}  # fmt: skip


def backups() -> list[dict[str, Any]]:
    """All backups, newest first."""
    if not BACKUP_DIR.exists():
        return []
    files = [p for p in BACKUP_DIR.glob("*.zip") if _NAME_RE.match(p.name)]
    return [info(p) for p in sorted(files, key=_age_key, reverse=True)]


def path_of(name: str) -> Path:
    if not _NAME_RE.match(name) or not (BACKUP_DIR / name).is_file():
        raise FileNotFoundError(f"Keine Sicherung „{name}“")
    return BACKUP_DIR / name


def auto_backup(max_age_hours: float = 20) -> dict[str, Any] | None:
    """A daily automatic backup (when there is data and the newest auto backup is older than ``max_age_hours``)."""
    if not _members():
        return None
    newest = max((p.stat().st_mtime for p in BACKUP_DIR.glob("auto-*.zip")), default=0) if BACKUP_DIR.exists() else 0
    if time.time() - newest < max_age_hours * 3600:
        return None
    return create("auto")


def _safe_members(z: zipfile.ZipFile) -> list[str]:
    names = []
    for n in z.namelist():
        p = PurePosixPath(n)
        if n.endswith("/"):
            continue
        if p.is_absolute() or ".." in p.parts or "\\" in n or ":" in n:
            raise ValueError(f"Ungültiger Pfad in der Sicherung: {n}")
        names.append(n)
    return names


def check(data: bytes) -> dict[str, Any]:
    """Validate an uploaded zip; returns its manifest."""
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest = json.loads(z.read("backup.json"))
            _safe_members(z)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError("Das ist keine Sicherung dieser App (backup.json fehlt oder Datei beschädigt).") from exc
    if manifest.get("app") != "mtgdeckbuilder" or int(manifest.get("format", 0)) > FORMAT:
        raise ValueError("Diese Sicherung stammt nicht von dieser App oder aus einer neueren Version.")
    return manifest


def upload(data: bytes) -> dict[str, Any]:
    check(data)
    path = _target("hochgeladen")
    path.write_bytes(data)
    return info(path)


def restore(name: str) -> dict[str, Any]:
    """Replace the current data with a backup's. A backup of the current state is made first."""
    from . import proxy

    src = path_of(name)
    check(src.read_bytes())
    safety = create("vor-wiederherstellung")
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(src) as z:
        names = _safe_members(z)
        z.extractall(tmp, members=names)
        root = Path(tmp)
        # decks: replace the whole directory (incl. versions, games, opponents, trash)
        if (root / "decks").exists():
            old = storage.DECKS_DIR.with_name(storage.DECKS_DIR.name + f".alt-{_stamp()}")
            if storage.DECKS_DIR.exists():
                storage.DECKS_DIR.rename(old)
            shutil.copytree(root / "decks", storage.DECKS_DIR)
            shutil.rmtree(old, ignore_errors=True)
        for zname, target in _files().items():
            if (root / zname).exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp_target = target.with_name(f".{target.name}.restore.tmp")
                shutil.copy2(root / zname, tmp_target)
                os.replace(tmp_target, target)
            elif target.exists():  # not in the backup = did not exist then
                target.unlink()
        orders = proxy.PROXIES_DIR / ".orders"
        if (root / "proxies" / ".orders").exists() or orders.exists():
            shutil.rmtree(orders, ignore_errors=True)
            if (root / "proxies" / ".orders").exists():
                shutil.copytree(root / "proxies" / ".orders", orders)
        for name in PROXY_FILES:
            for sel in (root / "proxies").glob(f"*/{name}") if (root / "proxies").exists() else []:
                dest = proxy.PROXIES_DIR / sel.parent.name / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sel, dest)
        for up in (root / "proxies").glob("*/uploads/*") if (root / "proxies").exists() else []:
            dest = proxy.PROXIES_DIR / up.parent.parent.name / "uploads" / up.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(up, dest)
    from .sync import service as sync_service

    sync_service.reset()  # synced devices: the restored data is merged with theirs, nothing is deleted elsewhere
    return {"restored": name, "safety_backup": safety["name"], "decks": len(storage.list_decks())}
