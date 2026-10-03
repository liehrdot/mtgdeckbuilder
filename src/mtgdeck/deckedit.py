"""Change a saved deck directly – add, remove, set quantities or categories – without an AI run.

Every edit resolves new names (German names too), re-validates the whole deck (legality, bracket,
budget, blacklist) and stores a new version with a change note. ``similar_cards`` suggests
replacements for one card (same role/tags, fits the colour identity, not in the deck).
"""

from __future__ import annotations

from typing import Any

from . import blacklist, carddb, scryfall, storage
from .cards import resolve
from .deck import ROLE_TAGS, card_roles, primary_type
from .power import PowerProfile
from .validate import validate_deck

# role -> deck category (fixed set from references/deck-template.md)
_ROLE_CATEGORY = [("board_wipe", "Board Wipe"), ("removal", "Removal"), ("counterspell", "Removal"),
                  ("ramp", "Ramp"), ("card_draw", "Draw"), ("protection", "Protection")]  # fmt: skip
_ROLE_TAGS_FLAT = {t for tags in ROLE_TAGS.values() for t in tags}
# role -> Scryfall tagger query (used without a local card DB)
_ROLE_QUERY = {"ramp": "otag:ramp", "card_draw": "otag:draw", "removal": "otag:removal", "board_wipe": "otag:sweeper",
               "counterspell": "otag:counterspell", "tutor": "otag:tutor", "protection": "otag:protects-permanent"}  # fmt: skip


def guess_category(card: dict[str, Any]) -> str:
    if "Land" in (card.get("type_line") or "").split("//")[0]:
        return "Land"
    roles = set(card.get("roles") or card_roles(card))
    return next((cat for role, cat in _ROLE_CATEGORY if role in roles), "Synergy")


async def revalidate(deck: dict[str, Any]) -> dict[str, Any]:
    """Validate ``deck['cards']`` and store the result in ``deck['validation']`` (not saved)."""
    lines = [f"{c.get('qty', 1)} {c['name']}" for c in deck.get("cards", [])]
    result = await validate_deck(
        deck["commanders"], lines, int(deck.get("bracket") or 3), currency=deck.get("currency", "eur"),
        budget=deck.get("budget"), proxy=bool(deck.get("proxy")),
        profile=PowerProfile(**deck["power_profile"]) if deck.get("power_profile") else None,
        table_rule=deck.get("table_rule"),
    )  # fmt: skip
    result.pop("_card_data", None)
    result.pop("cards", None)
    deck["validation"] = result
    return result


def _note(added: list[str], removed: list[str], changed: list[str], note: str) -> str:
    parts = []
    if added:
        parts.append("+ " + ", ".join(added))
    if removed:
        parts.append("− " + ", ".join(removed))
    if changed:
        parts.append(", ".join(changed))
    auto = "Manuell: " + "; ".join(parts) if parts else "Manuell bearbeitet"
    return f"{note.strip()} ({auto})" if note.strip() else auto


async def edit_deck(
    slug: str,
    *,
    add: list[dict[str, Any]] | None = None,
    remove: list[str] | None = None,
    set_qty: dict[str, int] | None = None,
    set_category: dict[str, str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Apply changes to a saved deck, re-validate and save it as a new version.

    ``add``: ``[{"name", "qty"=1, "category"=None}]`` (quantities add up), ``remove``: names (all
    copies), ``set_qty``: name -> quantity (0 removes), ``set_category``: name -> category.
    Raises ``ValueError`` for unknown card names or when nothing changes. If the deck was saved
    meanwhile (e.g. by a running Claude job), the change is applied again to the newer version.
    """
    for attempt in range(3):
        try:
            return await _edit_once(slug, add=add, remove=remove, set_qty=set_qty, set_category=set_category, note=note)
        except storage.ConflictError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


async def _edit_once(slug: str, *, add: list[dict[str, Any]] | None, remove: list[str] | None,
                     set_qty: dict[str, int] | None, set_category: dict[str, str] | None, note: str) -> dict[str, Any]:  # fmt: skip
    deck = storage.load(slug)
    start_version = deck.get("version")
    cards: dict[str, dict[str, Any]] = {c["name"]: dict(c) for c in deck.get("cards", [])}
    commanders = set(deck.get("commanders", []))
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    add = [a for a in (add or []) if (a.get("name") or "").strip()]
    if add:
        data, renames, missing = await resolve([a["name"] for a in add])
        if missing:
            raise ValueError(f"Karte nicht gefunden: {', '.join(missing)}")
        for a in add:
            name = renames.get(a["name"].strip(), a["name"].strip())
            if name in commanders:
                raise ValueError(f"{name} ist der Commander des Decks.")
            qty = max(1, int(a.get("qty") or 1))
            if name in cards:
                cards[name]["qty"] = int(cards[name].get("qty", 1)) + qty
            else:
                cards[name] = {"name": name, "qty": qty, "category": a.get("category") or guess_category(data.get(name, {}))}
            added.append(f"{qty}× {name}" if qty > 1 else name)

    for name in remove or []:
        if cards.pop(name, None) is not None:
            removed.append(name)

    for name, qty in (set_qty or {}).items():
        if name not in cards:
            continue
        old = int(cards[name].get("qty", 1))
        if qty <= 0:
            cards.pop(name)
            removed.append(name)
        elif qty != old:
            cards[name]["qty"] = int(qty)
            changed.append(f"{name} {old}→{qty}")

    for name, category in (set_category or {}).items():
        if name in cards and category and cards[name].get("category") != category:
            cards[name]["category"] = category
            changed.append(f"{name} → {category}")

    if not (added or removed or changed):
        raise ValueError("Keine Änderung.")
    deck["cards"] = sorted(cards.values(), key=lambda c: c["name"])
    result = await revalidate(deck)
    change_note = deck["change_note"] = _note(added, removed, changed, note)
    paths = storage.save(deck, expect_version=start_version)
    return {
        "slug": deck["slug"],
        "version": deck.get("version"),
        "change_note": change_note,
        "legal": result["legal"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "card_count": result["stats"]["card_count"] + len(deck.get("commanders", [])),
        "saved": paths,
    }


# --- replacement suggestions ----------------------------------------------------------------


def _deck_identity(commander_data: list[dict[str, Any]]) -> str:
    return "".join(sorted({c for card in commander_data for c in card.get("color_identity", [])}))


def _tag_order(tags: list[str]) -> list[str]:
    """Most specific tags first (fewest cards), ignoring tiny and huge ones."""
    counts = carddb.tag_counts(tags)
    usable = [t for t, n in counts.items() if 15 <= n <= 4000]
    return sorted(usable, key=lambda t: counts[t])


async def similar_cards(deck: dict[str, Any], card_name: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Cards that could replace ``card_name``: same roles/tags, inside the colour identity,
    commander-legal, not in the deck, not blacklisted – most popular (EDHREC rank) first."""
    names = deck.get("commanders", []) + [card_name]
    data, renames, _ = await resolve(names)
    target = data.get(renames.get(card_name, card_name))
    if not target:
        raise ValueError(f"Karte nicht gefunden: {card_name}")
    identity = _deck_identity([data[c] for c in deck.get("commanders", []) if c in data])
    in_deck = {c["name"] for c in deck.get("cards", [])} | set(deck.get("commanders", []))
    banned = blacklist.names_lower()
    user_rules = blacklist.rules()
    roles = target.get("roles") or card_roles(target)
    ptype = primary_type(target.get("type_line", ""))
    score: dict[str, float] = {}
    found: dict[str, dict[str, Any]] = {}
    reason: dict[str, str] = {}

    def offer(cards: list[dict[str, Any]], why: str, weight: float) -> None:
        for rank, c in enumerate(cards):
            n = c.get("name")
            if not n or n in in_deck or n.lower() in banned or n == target["name"] or blacklist.card_rules(c, active=user_rules, currency=deck.get("currency", "eur")):
                continue
            found.setdefault(n, c)
            reason.setdefault(n, why)
            score[n] = score.get(n, 0) + weight - rank * 0.01

    if carddb.available() and target.get("tags"):
        role_tags = [t for t in target["tags"] if t in _ROLE_TAGS_FLAT]
        for i, tag in enumerate(dict.fromkeys(role_tags + _tag_order(target["tags"])[:4])):
            hits = carddb.search(color_identity=identity, tags=[tag], limit=40)
            offer([h for h in hits if primary_type(h.get("type_line", "")) == ptype] or hits, f"Tag „{tag}“", 2.0 if i == 0 else 1.0)
    else:
        queries = [(_ROLE_QUERY[r], r) for r in roles if r in _ROLE_QUERY] or [(f"t:{ptype.lower()}", ptype)]
        for q, why in queries[:3]:
            res = await scryfall.search(f"{q} id<={identity or 'C'} f:commander -!\"{target['name']}\"", max_results=40)
            offer([scryfall.compact(c) for c in res["cards"]], f"gleiche Rolle: {why}", 1.0)

    best = sorted(score, key=lambda n: (-score[n], found[n].get("edhrec_rank") or 10**6))[:limit]
    keep = ("name", "type_line", "mana_cost", "cmc", "image", "image_back", "price_eur", "price_usd", "game_changer", "oracle_text")
    return [{**{k: found[n].get(k) for k in keep}, "reason": reason[n]} for n in best]


ROLE_LABELS = {"ramp": "Ramp", "card_draw": "Kartenzug", "removal": "Removal", "board_wipe": "Board Wipe",
               "counterspell": "Counterspell", "tutor": "Tutor", "protection": "Schutz"}  # fmt: skip


async def role_candidates(deck: dict[str, Any], role: str, *, limit: int = 18, max_price: float | None = None) -> list[dict[str, Any]]:
    """Popular cards for one role (ramp, card_draw, …) in the deck's colours that are not in the deck
    yet and not blacklisted – without an AI run (local DB tags, else a Scryfall tagger search)."""
    if role not in _ROLE_QUERY:
        raise ValueError(f"Unbekannte Rolle: {role}")
    data, _, _ = await resolve(deck.get("commanders", []))
    identity = _deck_identity(list(data.values()))
    in_deck = {c["name"] for c in deck.get("cards", [])} | set(deck.get("commanders", []))
    banned = blacklist.names_lower()
    user_rules = blacklist.rules()
    currency = deck.get("currency", "eur")
    tag = _ROLE_QUERY[role].split(":", 1)[1]
    if carddb.available() and carddb.tag_counts([tag]).get(tag):
        found = carddb.search(color_identity=identity, tags=[tag], max_price=max_price, currency=currency, limit=limit * 3)
    else:
        price = f" {'usd' if currency == 'usd' else 'eur'}<={max_price:g}" if max_price else ""
        res = await scryfall.search(f"{_ROLE_QUERY[role]} id<={identity or 'C'} f:commander -t:land{price}", max_results=limit * 3)
        found = [scryfall.compact(c) for c in res["cards"]]
    keep = ("name", "type_line", "mana_cost", "cmc", "image", "image_back", "price_eur", "price_usd", "game_changer", "oracle_text")
    out = [{**{k: c.get(k) for k in keep}, "reason": ROLE_LABELS.get(role, role)} for c in found
           if c.get("name") not in in_deck and c.get("name", "").lower() not in banned and not blacklist.card_rules(c, active=user_rules, currency=currency)
           and "Land" not in (c.get("type_line") or "").split("//")[0]]  # fmt: skip
    return out[:limit]
