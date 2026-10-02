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
    path = _path(order_id)
    if not path.exists():
        raise FileNotFoundError(f"Unbekannte Sammelbestellung: {order_id}")
    return json.loads(path.read_text("utf-8"))


def _save(order: dict[str, Any]) -> dict[str, Any]:
    order["updated"] = _now()
    path = _path(order["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(order, ensure_ascii=False, indent=2), "utf-8")
    return summary(order)


def create(name: str = "") -> dict[str, Any]:
    oid = uuid.uuid4().hex[:8]
    return _save({"id": oid, "slug": SLUG_PREFIX + oid, "name": name.strip()[:120] or "Sammelbestellung",
                  "created": _now(), "items": []})  # fmt: skip


def rename(order_id: str, name: str) -> dict[str, Any]:
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

    order = load(order_id)
    cards = [i for i in items if i.get("kind", "card") == "card" and str(i.get("name", "")).strip()]
    renames: dict[str, str] = {}
    if cards:
        found, renames, missing = await resolve([c["name"] for c in cards])
        if missing:
            raise ValueError(f"Nicht gefunden: {', '.join(missing[:10])}")
        known = set(found)
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


def added_since(deck_slug: str, since: int, *, only_missing: bool = False) -> list[dict[str, Any]]:
    """Positions for the cards a rebuild brought in: copies in the current deck that version ``since``
    did not have (commanders included), optionally capped by what the collection lacks."""
    from . import collection, storage

    deck = storage.load(deck_slug)
    old = storage.load_version(deck_slug, since)

    def counts(d: dict[str, Any]) -> dict[str, int]:
        out = {c: 1 for c in d.get("commanders", [])}
        for c in d.get("cards", []):
            out[c["name"]] = out.get(c["name"], 0) + int(c.get("qty", 1))
        return out

    before, now = counts(old), counts(deck)
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

