"""Three-way merges: base (last synced), local (this device) and remote (already on the server).

Rules:
- equal sides or only one side changed → take the changed side;
- objects key by key; lists of objects with ``id`` item by item (new items of both sides stay, deleted ones go,
  an item deleted on one side but changed on the other stays);
- other values both sides changed differently → the remote value wins and the local one is logged as a
  conflict (``Conflict``), so nothing is lost silently.

Decks (``merge_deck``): cards merge per card, history keeps the versions of both devices (local ones that never
reached the server are renumbered behind the remote ones) and a merged result becomes a new version.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass
class Conflict:
    path: str
    where: str
    kept: Any
    local: Any
    note: str = "beide Geräte haben geändert – die Fassung vom Server gilt"

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "where": self.where, "kept": self.kept, "local": self.local, "note": self.note}


@dataclass
class Log:
    path: str
    conflicts: list[Conflict] = field(default_factory=list)

    def add(self, where: str, kept: Any, local: Any, note: str | None = None) -> None:
        self.conflicts.append(Conflict(self.path, where, kept, local, *(n for n in [note] if n)))


def _is_id_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(i, dict) and "id" in i for i in value)


def merge_json(base: Any, local: Any, remote: Any, log: Log, where: str = "") -> Any:
    """Three-way merge of JSON values (``MISSING`` for a side that has no value)."""
    if local == remote:
        return deepcopy(local)
    if base == local:
        return deepcopy(remote)
    if base == remote:
        return deepcopy(local)
    if isinstance(local, dict) and isinstance(remote, dict):
        return _merge_dict(base if isinstance(base, dict) else {}, local, remote, log, where)
    if _is_id_list(local) and _is_id_list(remote) and (local or remote):
        return _merge_id_list(base if _is_id_list(base) else [], local, remote, log, where)
    log.add(where or "(gesamt)", remote, local)
    return deepcopy(remote)


def _merge_dict(base: dict, local: dict, remote: dict, log: Log, where: str) -> dict:
    out: dict[str, Any] = {}
    for key in [*remote, *(k for k in local if k not in remote)]:
        sub = f"{where}.{key}" if where else str(key)
        b = base.get(key, MISSING)
        in_l, in_r = key in local, key in remote
        if in_l and in_r:
            out[key] = merge_json(b, local[key], remote[key], log, sub)
        elif in_r:  # missing locally: deleted here (if the base had it) or added there
            if b is MISSING:
                out[key] = deepcopy(remote[key])
            elif remote[key] != b:  # deleted here, changed there -> keep the change
                log.add(sub, remote[key], None, "hier gelöscht, dort geändert – bleibt erhalten")
                out[key] = deepcopy(remote[key])
        else:  # missing remotely
            if b is MISSING:
                out[key] = deepcopy(local[key])
            elif local[key] != b:
                log.add(sub, local[key], local[key], "dort gelöscht, hier geändert – bleibt erhalten")
                out[key] = deepcopy(local[key])
    return out


def _merge_id_list(base: list, local: list, remote: list, log: Log, where: str) -> list:
    bmap = {i["id"]: i for i in base}
    lmap = {i["id"]: i for i in local}
    rmap = {i["id"]: i for i in remote}
    out: list[Any] = []
    for item in remote:
        iid = item["id"]
        sub = f"{where}[{iid}]"
        if iid in lmap:
            out.append(merge_json(bmap.get(iid, MISSING), lmap[iid], item, log, sub))
        elif iid in bmap:  # deleted here
            if item != bmap[iid]:
                log.add(sub, item, None, "hier gelöscht, dort geändert – bleibt erhalten")
                out.append(deepcopy(item))
        else:
            out.append(deepcopy(item))
    for item in local:
        iid = item["id"]
        if iid in rmap:
            continue
        if iid in bmap:  # deleted there
            if item != bmap[iid]:
                log.add(f"{where}[{iid}]", item, item, "dort gelöscht, hier geändert – bleibt erhalten")
                out.append(deepcopy(item))
        else:
            out.append(deepcopy(item))
    return out


def merge_lines(base: str | Any, local: str, remote: str) -> str:
    """Text as a set of lines (blacklist): additions of both sides stay, deletions of either side go."""
    def lines(text: Any) -> list[str]:
        return [ln.rstrip() for ln in text.splitlines() if ln.strip()] if isinstance(text, str) else []

    b, lo, r = lines(base), lines(local), lines(remote)
    bs, ls, rs = set(b), set(lo), set(r)
    keep = (ls & rs) | (ls - bs) | (rs - bs)
    out = list(dict.fromkeys([*(x for x in r if x in keep), *(x for x in lo if x in keep)]))
    return "\n".join(out) + ("\n" if out else "")


# --- decks --------------------------------------------------------------------------------------

DECK_SPECIAL = {"cards", "history", "version", "created", "updated", "validation", "slug", "last_job", "needs_revalidation"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cards_map(deck: Any) -> dict[str, Any]:
    return {c["name"]: c for c in (deck or {}).get("cards", [])} if isinstance(deck, dict) else {}


def _content(deck: dict[str, Any]) -> str:
    from ..storage import _CONTENT_KEYS

    return json.dumps({k: deck.get(k) for k in _CONTENT_KEYS}, sort_keys=True, default=str)


@dataclass
class DeckMerge:
    deck: dict[str, Any]
    renumber: dict[int, int]  # local version -> new number
    merge_version: int | None  # number of the new "Zusammengeführt" version (needs a snapshot)


def merge_deck(base: Any, local: dict[str, Any], remote: dict[str, Any], log: Log) -> DeckMerge:
    """Merge two changed copies of a deck that share ``base`` (see module doc)."""
    from ..storage import _metrics, diff, level_text

    b = base if isinstance(base, dict) else {}
    base_version = int(b.get("version") or 0)
    # cards: per card name; everything else except bookkeeping: generic
    cards = merge_json(_cards_map(base) or MISSING, _cards_map(local), _cards_map(remote), log, "cards")
    rest = merge_json({k: v for k, v in b.items() if k not in DECK_SPECIAL} if b else MISSING,
                      {k: v for k, v in local.items() if k not in DECK_SPECIAL},
                      {k: v for k, v in remote.items() if k not in DECK_SPECIAL}, log)  # fmt: skip
    merged = {**rest, "slug": remote.get("slug") or local.get("slug"), "cards": list(cards.values())}

    remote_history = list(remote.get("history") or [])
    remote_max = max([int(remote.get("version") or 0), *(int(h.get("version") or 0) for h in remote_history)])
    local_new = [h for h in local.get("history") or [] if int(h.get("version") or 0) > base_version]
    renumber = {int(h["version"]): remote_max + 1 + i for i, h in enumerate(local_new)}
    history = remote_history + [
        {**h, "version": renumber[int(h["version"])], "note": (h.get("note") or "")
         + (f" (auf diesem Gerät als v{h['version']} gespeichert)" if renumber[int(h["version"])] != int(h["version"]) else "")}
        for h in local_new
    ]  # fmt: skip
    last = local if local_new else remote  # the snapshot the history ends with
    version = remote_max + len(local_new)
    merge_version = None
    if _content(merged) != _content(last):
        version += 1
        merge_version = version
        change = diff(last, merged)
        history.append({"version": version, "at": _now(), "note": "Zusammengeführt (Änderungen von zwei Geräten)", **change,
                        "from": level_text(last), "to": level_text(merged), **_metrics(merged)})  # fmt: skip
    merged["history"] = history
    merged["version"] = version
    merged["created"] = remote.get("created") or local.get("created")
    merged["updated"] = _now() if merge_version else max(str(remote.get("updated") or ""), str(local.get("updated") or "")) or _now()
    same_as = next((d for d in (remote, local) if _content(d) == _content(merged)), None)
    merged["validation"] = (same_as or remote).get("validation")
    if same_as is None or same_as.get("needs_revalidation"):
        merged["needs_revalidation"] = True
    return DeckMerge(merged, renumber, merge_version)
