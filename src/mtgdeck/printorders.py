"""Collective print orders ("Sammelbestellung"): cards from several decks, single cards and tokens in
any amount, printed together like one deck (MPC Autofill order, home-printing PDF).

An order is stored as ``<proxies>/.orders/<id>.json``::

    {"id", "name", "created", "updated",
     "items": [{"id", "kind": "card"|"token", "name", "qty", "source", "source_slug",
                "type_line", "token_id", "image"}]}

``as_deck()`` turns it into the deck-shaped dict the print pipeline (``proxy.plan/prepare/export_pdf``)
works with: slug ``sammel-<id>`` (print folder ``proxies/sammel-<id>/``), the cards summed up by name and
the tokens with their own quantities in ``print_tokens``.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import proxy
from .jsonstore import locked, read_json, write_json

SLUG_PREFIX = "sammel-"
_ID_RE = re.compile(r"^[a-f0-9]{8}$")
MAX_QTY = 500


def _dir() -> Path:
    return proxy.PROXIES_DIR / ".orders"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def is_order_slug(slug: str) -> bool:
    return slug.startswith(SLUG_PREFIX) and bool(_ID_RE.match(slug[len(SLUG_PREFIX):]))


def _path(order_id: str) -> Path:
    oid = order_id.removeprefix(SLUG_PREFIX)
    if not _ID_RE.match(oid):
        raise FileNotFoundError(f"Unbekannte Sammelbestellung: {order_id}")
    return _dir() / f"{oid}.json"


def load(order_id: str) -> dict[str, Any]:
    order = read_json(_path(order_id))
    if order is None:
        raise FileNotFoundError(f"Unbekannte Sammelbestellung: {order_id}")
    return order


def _save(order: dict[str, Any]) -> dict[str, Any]:
    order["updated"] = _now()
    write_json(_path(order["id"]), order)
    return summary(order)


def create(name: str = "") -> dict[str, Any]:
    oid = uuid.uuid4().hex[:8]
    return _save({"id": oid, "slug": SLUG_PREFIX + oid, "name": name.strip()[:120] or "Sammelbestellung",
                  "created": _now(), "items": []})  # fmt: skip


def rename(order_id: str, name: str) -> dict[str, Any]:
    with locked(_path(order_id)):
        order = load(order_id)
        order["name"] = name.strip()[:120] or order["name"]
        return _save(order)


def delete(order_id: str) -> None:
    import shutil

    order = load(order_id)
    _path(order_id).unlink(missing_ok=True)
    shutil.rmtree(proxy.order_dir(order["slug"]), ignore_errors=True)  # print files of this order


def summary(order: dict[str, Any]) -> dict[str, Any]:
    """The order plus counts: cards, tokens, print slots and the MPC quantity bracket."""
    items = order.get("items", [])
    cards = sum(i["qty"] for i in items if i["kind"] == "card")
    tokens = sum(i["qty"] for i in items if i["kind"] == "token")
    return {**order, "slug": SLUG_PREFIX + order["id"], "counts": {
        "cards": cards, "tokens": tokens, "slots": cards + tokens, "entries": len(items),
        "mpc_bracket": proxy.mpc_bracket(cards + tokens) if cards + tokens else 0,
        "sources": sorted({i.get("source") or "" for i in items} - {""}),
    }}  # fmt: skip


def orders() -> list[dict[str, Any]]:
    if not _dir().exists():
        return []
    out = []
    for f in _dir().glob("*.json"):
        try:
            out.append(summary(json.loads(f.read_text("utf-8"))))
        except (ValueError, KeyError):
            continue
    return sorted(out, key=lambda o: o.get("updated") or "", reverse=True)


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("kind", "name", "source") if a["kind"] == "card" else ("kind", "name", "token_id", "source")
    return all(a.get(k) == b.get(k) for k in keys)


async def add(order_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Add cards (``kind`` card, German names resolve to Oracle names) and tokens (``kind`` token, any
    name; ``token_id`` = Scryfall id for the image). Equal entries from the same source add up.
    Raises ``ValueError`` for unknown card names."""
    from .cards import resolve

    load(order_id)  # unknown order -> FileNotFoundError before the lookups
    cards = [i for i in items if i.get("kind", "card") == "card" and str(i.get("name", "")).strip()]
    renames: dict[str, str] = {}
    known: set[str] = set()
    if cards:
        found, renames, missing = await resolve([c["name"] for c in cards])
        if missing:
            raise ValueError(f"Nicht gefunden: {', '.join(missing[:10])}")
        known = set(found)
    with locked(_path(order_id)):  # re-read after the lookups, so nothing added meanwhile is lost
        return _add_locked(order_id, items, renames, known)


def _add_locked(order_id: str, items: list[dict[str, Any]], renames: dict[str, str], known: set[str]) -> dict[str, Any]:
    order = load(order_id)
    added = 0
    for raw in items:
        kind = raw.get("kind", "card")
        name = str(raw.get("name", "")).strip()
        qty = int(raw.get("qty") or 1)
        if not name or qty < 1 or kind not in ("card", "token"):
            continue
        if kind == "card":
            name = renames.get(name, name)
            if name not in known:
                continue
        item = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": name, "qty": min(qty, MAX_QTY),
                "source": str(raw.get("source") or ("Tokens" if kind == "token" else "Einzelkarten"))[:80],
                "source_slug": raw.get("source_slug") or None}  # fmt: skip
        if kind == "token":
            item.update({"type_line": raw.get("type_line") or "Token", "token_id": raw.get("token_id") or None,
                         "image": raw.get("image") or None})  # fmt: skip
        existing = next((i for i in order["items"] if _same(i, item)), None)
        if existing:
            existing["qty"] = min(existing["qty"] + item["qty"], MAX_QTY)
        else:
            order["items"].append(item)
        added += item["qty"]
    result = _save(order)
    result["added"] = added
    return result


def update_item(order_id: str, item_id: str, qty: int) -> dict[str, Any]:
    with locked(_path(order_id)):
        return _update_item(order_id, item_id, qty)


def _update_item(order_id: str, item_id: str, qty: int) -> dict[str, Any]:
    order = load(order_id)
    item = next((i for i in order["items"] if i["id"] == item_id), None)
    if item is None:
        raise FileNotFoundError("Position nicht gefunden")
    if qty <= 0:
        order["items"].remove(item)
    else:
        item["qty"] = min(qty, MAX_QTY)
    return _save(order)


def remove(order_id: str, *, item_id: str | None = None, source: str | None = None) -> dict[str, Any]:
    """Remove one position (``item_id``) or every position of one ``source`` (e.g. a deck)."""
    with locked(_path(order_id)):
        order = load(order_id)
        before = len(order["items"])
        order["items"] = [i for i in order["items"] if not ((item_id and i["id"] == item_id) or (source and i.get("source") == source))]
        if len(order["items"]) == before:
            raise FileNotFoundError("Position nicht gefunden")
        return _save(order)


def deck_items(deck: dict[str, Any], *, names: list[str] | None = None, only_missing: bool = False) -> list[dict[str, Any]]:
    """Positions for (some) cards of a deck: all copies, or only the ones the collection lacks."""
    from . import collection

    wanted = set(names) if names else None
    missing = collection.missing_counts(deck) if only_missing else None
    out = []
    for c in [*({"name": n, "qty": 1} for n in deck.get("commanders", [])), *deck.get("cards", [])]:
        if wanted is not None and c["name"] not in wanted:
            continue
        qty = int(c.get("qty", 1)) if missing is None else missing.get(c["name"], 0)
        if qty > 0:
            out.append({"kind": "card", "name": c["name"], "qty": qty, "source": deck.get("name") or deck["slug"],
                        "source_slug": deck["slug"]})  # fmt: skip
    return out


def to_moxfield(order_id: str) -> tuple[str, int]:
    """The order's cards as a Moxfield/Archidekt/ManaBox text list ("2 Sol Ring", summed over sources, sorted).
    Tokens are left out – Moxfield imports no tokens. Returns (text, number of token positions left out)."""
    order = load(order_id)
    cards: dict[str, int] = {}
    tokens = 0
    for i in order["items"]:
        if i["kind"] == "card":
            cards[i["name"]] = cards.get(i["name"], 0) + int(i["qty"])
        else:
            tokens += 1
    text = "".join(f"{q} {n}\n" for n, q in sorted(cards.items(), key=lambda kv: kv[0].lower()))
    return text, tokens


def as_deck(order_id: str) -> dict[str, Any]:
    """The deck-shaped dict for the print pipeline (cards summed by name, tokens with own quantities)."""
    order = load(order_id)
    cards: dict[str, int] = {}
    tokens: dict[tuple[str, str | None], dict[str, Any]] = {}
    for i in order["items"]:
        if i["kind"] == "card":
            cards[i["name"]] = cards.get(i["name"], 0) + i["qty"]
        else:
            key = (i["name"], i.get("token_id"))
            t = tokens.setdefault(key, {"name": i["name"], "type_line": i.get("type_line") or "Token", "qty": 0,
                                        "id": i.get("token_id"), "image": i.get("image"), "from": []})  # fmt: skip
            t["qty"] += i["qty"]
            if i.get("source") and i["source"] not in t["from"]:
                t["from"].append(i["source"])
    return {"slug": SLUG_PREFIX + order["id"], "name": order["name"], "commanders": [], "order": True,
            "cards": [{"name": n, "qty": q} for n, q in sorted(cards.items())], "print_tokens": list(tokens.values())}  # fmt: skip


def card_counts(deck: dict[str, Any]) -> dict[str, int]:
    """Card name -> copies in a deck, commanders included."""
    out = {c: 1 for c in deck.get("commanders", [])}
    for c in deck.get("cards", []):
        out[c["name"]] = out.get(c["name"], 0) + int(c.get("qty", 1))
    return out


def _deck_ref(deck: dict[str, Any]) -> dict[str, Any]:
    return {"slug": deck["slug"], "name": deck.get("name") or deck["slug"], "commanders": deck.get("commanders", []),
            "version": deck.get("version")}  # fmt: skip


def compare_candidates(deck_slug: str) -> list[dict[str, Any]]:
    """The other saved decks, best match first: the deck this one was copied from, then by shared cards."""
    from . import storage

    deck = storage.load(deck_slug)
    mine = card_counts(deck)
    source = (deck.get("copied_from") or {}).get("slug")
    out = []
    for d in storage.list_decks():
        if d["slug"] == deck["slug"] or d.get("damaged"):
            continue
        try:
            other = card_counts(storage.load(d["slug"]))
        except (FileNotFoundError, ValueError):
            continue
        common = sum(min(q, other.get(n, 0)) for n, q in mine.items())
        same_cmd = bool(set(d.get("commanders") or []) & set(deck.get("commanders") or []))
        out.append({"slug": d["slug"], "name": d["name"], "commanders": d.get("commanders") or [], "common": common,
                    "copied_from": d["slug"] == source, "same_commander": same_cmd})  # fmt: skip
    return sorted(out, key=lambda c: (c["copied_from"], c["common"], c["same_commander"]), reverse=True)


def compare(deck_slug: str, other_slug: str) -> dict[str, Any]:
    """What ``deck_slug`` needs beyond ``other_slug`` – e.g. a rebuild that takes the cards of an old deck.

    ``added``: copies only the deck has (with ``owned`` copies in the collection, ``missing`` = what the
    collection does not cover, ``basic``); ``removed``: copies only the other deck has (they become free);
    ``common``: shared copies."""
    from . import collection, storage
    from .collection import BASIC_LANDS

    deck, other = storage.load(deck_slug), storage.load(other_slug)
    if deck["slug"] == other["slug"]:
        raise ValueError("Wähle ein anderes Deck zum Vergleichen.")
    mine, theirs = card_counts(deck), card_counts(other)
    owned = collection.owned_counts()
    missing = collection.missing_counts(deck)
    added, removed, common = [], [], []
    for name in sorted(set(mine) | set(theirs)):
        a, b = mine.get(name, 0), theirs.get(name, 0)
        if min(a, b):
            common.append({"name": name, "qty": min(a, b)})
        if a > b:
            o = owned.get(name, {"real": 0, "proxy": 0})
            added.append({"name": name, "qty": a - b, "owned": o["real"] + o["proxy"], "missing": min(a - b, missing.get(name, 0)),
                          "basic": name in BASIC_LANDS, "commander": name in deck.get("commanders", [])})  # fmt: skip
        elif b > a:
            removed.append({"name": name, "qty": b - a})
    return {"deck": _deck_ref(deck), "other": _deck_ref(other), "added": added, "removed": removed, "common": common,
            "totals": {"added": sum(i["qty"] for i in added), "missing": sum(i["missing"] for i in added),
                       "removed": sum(i["qty"] for i in removed), "common": sum(i["qty"] for i in common)}}  # fmt: skip


def compare_items(deck_slug: str, other_slug: str, *, only_missing: bool = False,
                  names: list[str] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Order positions for the cards a deck needs beyond another deck (optionally only what the collection lacks)."""
    diff = compare(deck_slug, other_slug)
    wanted = set(names) if names else None
    source = f"{diff['deck']['name']} (statt {diff['other']['name']})"
    out = []
    for i in diff["added"]:
        qty = i["missing"] if only_missing else i["qty"]
        if qty > 0 and (wanted is None or i["name"] in wanted):
            out.append({"kind": "card", "name": i["name"], "qty": qty, "source": source, "source_slug": diff["deck"]["slug"]})
    return out


def added_since(deck_slug: str, since: int, *, only_missing: bool = False) -> list[dict[str, Any]]:
    """Positions for the cards a rebuild brought in: copies in the current deck that version ``since``
    did not have (commanders included), optionally capped by what the collection lacks."""
    from . import collection, storage

    deck = storage.load(deck_slug)
    old = storage.load_version(deck_slug, since)
    before, now = card_counts(old), card_counts(deck)
    missing = collection.missing_counts(deck) if only_missing else None
    out = []
    for name, qty in now.items():
        new = qty - before.get(name, 0)
        if missing is not None:
            new = min(new, missing.get(name, 0))
        if new > 0:
            out.append({"kind": "card", "name": name, "qty": new, "source": f"{deck.get('name') or deck_slug} (neu in v{deck.get('version')})",
                        "source_slug": deck_slug})  # fmt: skip
    return sorted(out, key=lambda i: i["name"])

