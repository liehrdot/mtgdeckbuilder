"""Resolve card names to compact card data: local bulk DB first, Scryfall API as fallback."""

from __future__ import annotations

from typing import Any

from . import carddb, glossary, scryfall
from .deck import card_roles


async def resolve(names: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[str]]:
    """Return (cards by English name, renames {query -> English name}, names not found).

    Names in other languages (e.g. German) resolve via the local DB built from Scryfall's
    All Cards bulk file.
    """
    queries = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    found: list[dict[str, Any]] = []
    missing = queries
    if carddb.available():
        found, missing = carddb.lookup(queries)
        await _complete_double_faced(found)
    if missing:
        raw, missing = await scryfall.collection(missing)
        found += [scryfall.compact(c) for c in raw]

    by_name: dict[str, dict[str, Any]] = {}
    renames: dict[str, str] = {}
    for c in found:
        query = c.pop("_query_name", c["name"])
        if query != c["name"]:
            renames[query] = c["name"]
        c["roles"] = card_roles(c)
        by_name[c["name"]] = c
    return by_name, renames, missing


async def deck_tokens(names: list[str]) -> list[dict[str, Any]]:
    """Tokens, emblems and markers the given cards create, merged by name and type:
    ``[{"name", "type_line", "id", "image", "from": [card names]}]``."""
    data, _, _ = await resolve(names)
    stale = [n for n, c in data.items() if "tokens" not in c]  # DB built before schema v3
    if stale:
        try:
            raw, _ = await scryfall.collection(stale)
            for r in raw:
                if r["name"] in data:
                    data[r["name"]]["tokens"] = scryfall.compact(r)["tokens"]
        except Exception:  # offline: tokens of these cards stay unknown
            pass
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for name, c in data.items():
        for t in c.get("tokens") or []:
            entry = merged.setdefault((t["name"], t["type_line"]), {**t, "image": scryfall.image_url(t["id"]) if t.get("id") else None, "from": []})
            if name not in entry["from"]:
                entry["from"].append(name)
    return sorted(merged.values(), key=lambda t: (not t["type_line"].startswith("Token"), t["name"]))


def _faces(raw: dict[str, Any] | None, prefix: str = "") -> list[dict[str, str]]:
    """Per-face name/type/text of a raw Scryfall card (``prefix="printed_"`` for the translated text)."""
    parts = (raw or {}).get("card_faces") or [raw or {}]
    text_key = f"{prefix}text" if prefix else "oracle_text"
    return [{"name": f.get(f"{prefix}name") or f.get("name", ""), "type_line": f.get(f"{prefix}type_line") or f.get("type_line", ""),
             "mana_cost": f.get("mana_cost", ""), "text": f.get(text_key) or ""} for f in parts]  # fmt: skip


async def card_text(name: str, lang: str = "de") -> dict[str, Any] | None:
    """Rules text of a card for the card view: ``faces`` (English Oracle text per face), ``printed``
    (the newest printing in ``lang``, None if there is none or Scryfall is unreachable) and the
    glossary ``terms`` the card uses."""
    found, _, _ = await resolve([name])
    if not found:
        return None
    c = next(iter(found.values()))
    raw: dict[str, Any] | None = None
    printed: dict[str, Any] | None = None
    if lang and lang != "en":
        try:
            page = await scryfall.search_page(f'!"{c["name"]}" lang:{lang}', order="released", unique="prints")
        except Exception:  # offline: English only
            page = {"cards": []}
        for r in page["cards"]:
            raw = raw or r
            faces = _faces(r, "printed_")
            if any(f["text"] for f in faces) or r.get("printed_name") or any(f.get("printed_name") for f in r.get("card_faces") or []):
                printed = {"lang": r.get("lang", lang), "set_name": r.get("set_name"), "faces": faces}
                break
    if raw is None and " // " in c["name"]:  # per-face Oracle text is not in the compact data
        try:
            raw = await scryfall.named(c["name"], fuzzy=False)
        except Exception:
            raw = None
    faces = _faces(raw) if raw else [{"name": c["name"], "type_line": c.get("type_line", ""), "mana_cost": c.get("mana_cost", ""),
                                      "text": c.get("oracle_text") or ""}]  # fmt: skip
    texts = [f["text"] for f in faces] + [f["text"] for f in (printed or {}).get("faces", [])]
    return {"name": c["name"], "pt": c.get("pt"), "faces": faces, "printed": printed,
            "terms": glossary.find_terms(*texts, keywords=(raw or {}).get("keywords"))}  # fmt: skip


async def _complete_double_faced(cards: list[dict[str, Any]]) -> None:
    """Cards from a DB built before layout/image_back existed: fetch those fields live for
    multi-face cards (otherwise their back faces would be missing in previews and prints)."""
    stale = [c for c in cards if " // " in c.get("name", "") and "layout" not in c]
    if not stale:
        return
    try:
        raw, _ = await scryfall.collection([c["name"] for c in stale])
    except Exception:  # offline: keep what we have
        return
    fresh = {r["name"]: scryfall.compact(r) for r in raw}
    for c in stale:
        if f := fresh.get(c["name"]):
            c.update({k: f[k] for k in ("layout", "image", "image_back") if k in f})
