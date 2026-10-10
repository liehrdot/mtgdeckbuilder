"""One device's side of the sync: pull → merge → push, repeated until nothing conflicts any more.

State in ``Roots.state`` (``.sync/``):
- ``state.json`` – device id, cursor (last server sequence seen), per path the base ``seq``/``hash``, a stat cache;
- ``base/<path>`` – the last synced content of each JSON/text document (for three-way merges);
- ``conflicts.json`` – what was decided on conflicts (shown in the app).

Safety rules:
- local files are only written under the app's own lock and only if they did not change since the scan
  (otherwise the document is skipped and handled in the next round);
- a deck deleted on another device goes to the local trash, not away;
- a document deleted on one device but changed on another is kept.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..jsonstore import locked, read_json, write_json
from . import files
from .files import DECK_RE, SNAPSHOT_RE, Roots, canonical, decode, digest, kind_of, snapshot_path
from .merge import MISSING, Log, merge_deck, merge_json, merge_lines
from .store import SyncConflict

ROUNDS = 5
PUSH_ORDER = {"snapshot": 0, "blob": 1, "json": 2, "lines": 2, "deck": 3}
CONFLICT_LOG_MAX = 300


class Transport(Protocol):
    def changes(self, since: int) -> dict[str, Any]: ...
    def put(self, doc: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Report:
    pulled: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    revalidate: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: list(dict.fromkeys(v)) if k not in ("conflicts",) else v for k, v in self.__dict__.items()}


@dataclass
class Local:
    kind: str
    hash: str
    file: Path


class SyncClient:
    def __init__(self, roots: Roots, transport: Transport, device: str = ""):
        self.roots = roots
        self.transport = transport
        self.state_file = roots.state / "state.json"
        self.state: dict[str, Any] = {}
        self.device_name = device

    # --- state -----------------------------------------------------------------------------------

    def reset_state(self) -> None:
        """Forget what was synced (cursor, bases) but not the local files: the next sync merges everything with the
        server as if both sides had created it independently (lists are joined, decks find their last common
        version). Used for a different server and after restoring a backup – so nothing gets deleted elsewhere."""
        self.roots.state.mkdir(parents=True, exist_ok=True)
        with locked(self.state_file):
            device = (read_json(self.state_file, {}) or {}).get("device")
            shutil.rmtree(self.roots.state / "base", ignore_errors=True)
            write_json(self.state_file, {"device": device} if device else {}, indent=None)

    def cursor(self) -> int:
        return int((read_json(self.state_file, {}) or {}).get("cursor") or 0)

    def _load_state(self) -> None:
        st = read_json(self.state_file, {}) or {}
        st.setdefault("device", uuid.uuid4().hex[:12])
        st.setdefault("cursor", 0)
        st.setdefault("docs", {})
        st.setdefault("cache", {})
        self.state = st

    def _save_state(self) -> None:
        write_json(self.state_file, self.state, indent=None)

    def _base_file(self, path: str) -> Path:
        return self.roots.state.joinpath("base", *path.split("/"))

    def _base_value(self, path: str, kind: str) -> Any:
        st = self.state["docs"].get(path)
        if not st or st.get("deleted") or kind == "blob":
            return MISSING
        try:
            return decode(kind, self._base_file(path).read_bytes())
        except (FileNotFoundError, ValueError):
            return MISSING

    def _set_base(self, path: str, kind: str, seq: int, data: bytes | None) -> None:
        deleted = data is None
        self.state["docs"][path] = {"seq": seq, "kind": kind, "deleted": deleted, "hash": None if deleted else digest(data)}
        bf = self._base_file(path)
        if deleted or kind == "blob":
            bf.unlink(missing_ok=True)
        else:
            files._write_bytes(bf, data)

    def _base_hash(self, path: str) -> str | None:
        st = self.state["docs"].get(path)
        return None if not st or st.get("deleted") else st.get("hash")

    # --- local files -----------------------------------------------------------------------------

    def _scan(self, report: Report) -> dict[str, Local]:
        out: dict[str, Local] = {}
        cache = self.state["cache"]
        fresh: dict[str, list[Any]] = {}
        for path, p in self.roots.scan().items():
            kind = kind_of(path)
            try:
                st = p.stat()
                c = cache.get(path)
                if c and c[0] == st.st_mtime_ns and c[1] == st.st_size:
                    h = c[2]
                else:
                    h = files.local_hash(kind, p)
                fresh[path] = [st.st_mtime_ns, st.st_size, h]
                out[path] = Local(kind, h, p)
            except (OSError, ValueError) as exc:
                report.errors.append(f"{path}: nicht lesbar ({exc}) – wird übersprungen")
        self.state["cache"] = fresh
        return out

    def _current_hash(self, path: str, kind: str) -> str | None:
        p = self.roots.physical(path)
        if not p.exists():
            return None
        try:
            return files.local_hash(kind, p)
        except (OSError, ValueError):
            return "unreadable"

    def _remember(self, path: str, kind: str) -> None:
        p = self.roots.physical(path)
        if p.exists():
            st = p.stat()
            self.state["cache"][path] = [st.st_mtime_ns, st.st_size, files.local_hash(kind, p)]
        else:
            self.state["cache"].pop(path, None)

    def _write(self, path: str, kind: str, value: Any, expect: str | None, report: Report) -> bool:
        """Write (or delete with ``value is None``) unless the file changed since we looked at it."""
        with files.guarded(self.roots, path):
            if self._current_hash(path, kind) != expect:
                report.skipped.append(path)
                return False
            if value is None:
                files.delete_local(self.roots, path, kind)
            else:
                files.write_local(self.roots, path, kind, value)
            self._remember(path, kind)
        return True

    # --- sync ------------------------------------------------------------------------------------

    def sync(self) -> dict[str, Any]:
        """One full sync; returns what happened (pulled, pushed, merged, conflicts, skipped, errors …)."""
        self.roots.state.mkdir(parents=True, exist_ok=True)
        report = Report()
        with locked(self.state_file):
            self._load_state()
            try:
                for _ in range(ROUNDS):
                    self._pull(report)
                    if not self._push(report):
                        break
            finally:
                self._save_state()
                self._log_conflicts(report)
        return {**report.as_dict(), "device": self.state.get("device"), "cursor": self.state.get("cursor")}

    def _log_conflicts(self, report: Report) -> None:
        if not report.conflicts:
            return
        path = self.roots.state / "conflicts.json"
        with locked(path):
            items = read_json(path, []) or []
            items += [{**c, "at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())} for c in report.conflicts]
            write_json(path, items[-CONFLICT_LOG_MAX:])

    # --- pull ------------------------------------------------------------------------------------

    def _pull(self, report: Report) -> None:
        local = self._scan(report)
        since = int(self.state["cursor"])
        groups: dict[str, dict[str, Any]] = {}
        skipped_seqs: list[int] = []
        while True:
            page = self.transport.changes(since)
            for doc in page["docs"]:
                if not self._handle(doc, local, groups, report):
                    skipped_seqs.append(doc["seq"])
            since = page["cursor"]
            if not page.get("more"):
                break
        for slug, group in groups.items():
            if not self._merge_deck_group(slug, group, local, report):
                skipped_seqs += [d["seq"] for d in group["docs"]]
        self.state["cursor"] = (min(skipped_seqs) - 1) if skipped_seqs else since

    def _handle(self, doc: dict[str, Any], local: dict[str, Local], groups: dict[str, dict[str, Any]], report: Report) -> bool:
        """Apply one remote document; ``False`` = could not be applied now (retry next sync)."""
        path, kind = doc["path"], doc["kind"]
        if kind_of(path) != kind:
            return True  # unknown to this version of the app
        st = self.state["docs"].get(path)
        if st and st["seq"] >= doc["seq"]:
            return True
        data = None if doc["deleted"] else doc["data"]
        remote_hash = None if data is None else digest(data)
        loc = local.get(path)
        local_hash = loc.hash if loc else None
        base_hash = self._base_hash(path)
        if local_hash == remote_hash:
            self._set_base(path, kind, doc["seq"], data)
            return True
        local_changed = local_hash != base_hash
        if kind == "snapshot" and not local_changed:  # its deck changed here: the snapshot belongs to the deck merge
            slug = SNAPSHOT_RE.fullmatch(path).group(1)  # type: ignore[union-attr]
            deck_loc = local.get(f"decks/{slug}.json")
            local_changed = (deck_loc.hash if deck_loc else None) != self._base_hash(f"decks/{slug}.json")
        if kind in ("deck", "snapshot") and local_changed:
            m = DECK_RE.fullmatch(path) or SNAPSHOT_RE.fullmatch(path)
            group = groups.setdefault(m.group(1), {"deck": None, "snaps": [], "docs": []})  # type: ignore[union-attr]
            group["docs"].append(doc)
            if kind == "deck":
                group["deck"] = doc
            else:
                group["snaps"].append(doc)
            return True
        if not local_changed:  # only the server changed: take it
            if not self._write(path, kind, None if data is None else decode(kind, data), local_hash, report):
                return False
            self._set_base(path, kind, doc["seq"], data)
            report.pulled.append(path)
            return True
        log = Log(path)
        if data is None:  # deleted there, changed here: keep ours (pushed again)
            log.add("(gesamt)", "behalten", "behalten", "dort gelöscht, hier geändert – bleibt erhalten")
            self._set_base(path, kind, doc["seq"], None)
        elif loc is None:  # deleted here, changed there: restore
            if not self._write(path, kind, decode(kind, data), None, report):
                return False
            log.add("(gesamt)", "wiederhergestellt", None, "hier gelöscht, dort geändert – wiederhergestellt")
            self._set_base(path, kind, doc["seq"], data)
            report.pulled.append(path)
        else:
            mine = files.read_local(kind, loc.file)
            theirs = decode(kind, data)
            if kind == "json":
                merged = merge_json(self._base_value(path, kind), mine, theirs, log)
            elif kind == "lines":
                merged = merge_lines(self._base_value(path, kind), mine, theirs)
            else:  # blob: the server's version wins
                merged = theirs
                log.add("(Bild)", "vom Server", "hier ersetzt")
            if digest(canonical(kind, merged)) != local_hash and not self._write(path, kind, merged, local_hash, report):
                return False
            self._set_base(path, kind, doc["seq"], data)
            report.merged.append(path)
        report.conflicts += [c.as_dict() for c in log.conflicts]
        return True

    # --- decks -----------------------------------------------------------------------------------

    def _unsynced_snapshots(self, slug: str) -> dict[int, dict[str, Any]]:
        """Snapshots of a deck that exist here but never reached the server."""
        out = {}
        folder = self.roots.decks / ".versions" / slug
        for p in folder.glob("v*.json") if folder.is_dir() else []:
            m = re.fullmatch(r"v(\d+)\.json", p.name)
            if m and self._base_hash(snapshot_path(slug, int(m.group(1)))) is None:
                try:
                    out[int(m.group(1))] = json.loads(p.read_text("utf-8"))
                except ValueError:
                    continue
        return out

    def _common_version(self, slug: str) -> dict[str, Any] | None:
        """The newest version snapshot this device and the server have identically (a common ancestor for decks
        without a base, e.g. after ``reset_state``); ``None`` when there is none (really two different decks)."""
        best: tuple[int, Path] | None = None
        for path, st in self.state["docs"].items():
            m = SNAPSHOT_RE.fullmatch(path)
            if not m or m.group(1) != slug or st.get("deleted") or not st.get("hash"):
                continue
            p = self.roots.physical(path)
            try:
                if not p.exists() or files.local_hash("snapshot", p) != st["hash"]:
                    continue
            except (OSError, ValueError):
                continue
            if best is None or int(m.group(2)) > best[0]:
                best = (int(m.group(2)), p)
        return json.loads(best[1].read_text("utf-8")) if best else None

    def _merge_deck_group(self, slug: str, group: dict[str, Any], local: dict[str, Local], report: Report) -> bool:
        deck_path = f"decks/{slug}.json"
        ddoc = group["deck"]
        snaps = sorted(group["snaps"], key=lambda d: d["path"])
        if ddoc is None:  # only colliding snapshots arrived, the deck itself not (yet): try again later
            return False
        loc = local.get(deck_path)
        log = Log(deck_path)
        with files.guarded(self.roots, deck_path):
            if self._current_hash(deck_path, "deck") != (loc.hash if loc else None):
                report.skipped.append(deck_path)
                return False
            remote = None if ddoc["deleted"] else decode("deck", ddoc["data"])
            mine = files.read_local("deck", loc.file) if loc else None
            base = self._base_value(deck_path, "deck")
            if base is MISSING and mine is not None and remote is not None:
                base = self._common_version(slug) or MISSING  # after a reset: the last version both still share
            pending = self._unsynced_snapshots(slug)
            if remote is None:  # deleted there, changed here: keep ours, with all its snapshots (pushed again)
                log.add("(Deck)", "behalten", "behalten", "auf einem anderen Gerät gelöscht, hier geändert – bleibt erhalten")
                self._set_base(deck_path, "deck", ddoc["seq"], None)
                for sdoc in snaps:
                    self._set_base(sdoc["path"], "snapshot", sdoc["seq"], None)
            elif mine is None:  # deleted here, changed there: restore
                self._apply_deck(deck_path, remote, ddoc, snaps, report)
                log.add("(Deck)", "wiederhergestellt", None, "hier gelöscht, dort geändert – wiederhergestellt")
            elif base is MISSING:  # created independently on two devices with the same name
                new_slug = self._free_slug(slug)
                files.write_local(self.roots, f"decks/{new_slug}.json", "deck", {**mine, "slug": new_slug})
                for v, snap in pending.items():
                    files.write_local(self.roots, snapshot_path(new_slug, v), "snapshot", {**snap, "slug": new_slug})
                    (self.roots.decks / ".versions" / slug / f"v{v:04d}.json").unlink(missing_ok=True)
                log.add("(Deck)", slug, new_slug, f"auf zwei Geräten gleich benannt angelegt – dieses heißt jetzt „{new_slug}“")
                self._apply_deck(deck_path, remote, ddoc, snaps, report)
            else:
                dm = merge_deck(base, mine, remote, log)
                live = [d for d in snaps if not d["deleted"]]
                for sdoc in live:  # the server's snapshots win their numbers
                    files.write_local(self.roots, sdoc["path"], "snapshot", decode("snapshot", sdoc["data"]))
                    self._set_base(sdoc["path"], "snapshot", sdoc["seq"], sdoc["data"])
                for sdoc in snaps:
                    if sdoc["deleted"]:
                        self._set_base(sdoc["path"], "snapshot", sdoc["seq"], None)
                taken = {int(SNAPSHOT_RE.fullmatch(s["path"]).group(2)) for s in live}  # type: ignore[union-attr]
                for old, new in sorted(dm.renumber.items()):
                    snap = pending.get(old)
                    if snap is not None:
                        files.write_local(self.roots, snapshot_path(slug, new), "snapshot", {**snap, "version": new})
                for old in pending:
                    if old not in taken and old not in dm.renumber.values() and old != dm.merge_version:
                        (self.roots.decks / ".versions" / slug / f"v{old:04d}.json").unlink(missing_ok=True)
                if dm.merge_version:
                    files.write_local(self.roots, snapshot_path(slug, dm.merge_version), "snapshot",
                                      {k: v for k, v in dm.deck.items() if k != "history"})  # fmt: skip
                files.write_local(self.roots, deck_path, "deck", dm.deck)
                self._set_base(deck_path, "deck", ddoc["seq"], ddoc["data"])
                if dm.deck.get("needs_revalidation"):
                    report.revalidate.append(slug)
                report.merged.append(deck_path)
            self._remember(deck_path, "deck")
        report.conflicts += [c.as_dict() for c in log.conflicts]
        return True

    def _apply_deck(self, deck_path: str, remote: dict[str, Any], ddoc: dict[str, Any], snaps: list[dict[str, Any]], report: Report) -> None:
        for sdoc in snaps:
            if sdoc["deleted"]:
                self._set_base(sdoc["path"], "snapshot", sdoc["seq"], None)
                continue
            files.write_local(self.roots, sdoc["path"], "snapshot", decode("snapshot", sdoc["data"]))
            self._set_base(sdoc["path"], "snapshot", sdoc["seq"], sdoc["data"])
        files.write_local(self.roots, deck_path, "deck", remote)
        self._set_base(deck_path, "deck", ddoc["seq"], ddoc["data"])
        report.pulled.append(deck_path)

    def _free_slug(self, slug: str) -> str:
        n = 2
        while (self.roots.decks / f"{slug}-{n}.json").exists() or f"decks/{slug}-{n}.json" in self.state["docs"]:
            n += 1
        return f"{slug}-{n}"

    # --- push ------------------------------------------------------------------------------------

    def _push(self, report: Report) -> bool:
        """Send local changes; ``True`` when something conflicted (another round is needed)."""
        local = self._scan(report)
        paths = set(local) | {p for p, st in self.state["docs"].items() if not st.get("deleted")}
        retry = False
        for path in sorted(paths, key=lambda p: (PUSH_ORDER.get(kind_of(p) or "json", 2), p)):
            kind = kind_of(path)
            if kind is None:
                continue
            loc = local.get(path)
            if (loc.hash if loc else None) == self._base_hash(path):
                continue
            st = self.state["docs"].get(path)
            data = canonical(kind, files.read_local(kind, loc.file)) if loc else None
            if data is not None and digest(data) != loc.hash:  # changed during the sync: next time
                report.skipped.append(path)
                continue
            try:
                res = self.transport.put({"path": path, "kind": kind, "data": data, "deleted": data is None,
                                          "base_seq": st["seq"] if st else None, "device": self.state["device"]})  # fmt: skip
            except SyncConflict:
                retry = True
                continue
            except Exception as exc:  # server unreachable etc.: keep it for the next sync
                report.errors.append(f"{path}: {exc}")
                return False
            self._set_base(path, kind, res["seq"], data)
            report.pushed.append(path)
        return retry
