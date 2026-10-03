"""MCP server 'mtg': Commander deckbuilding tools for Claude Code.

Data sources (all free, no API keys): Scryfall (API + bulk data), EDHREC (public JSON),
Commander Spellbook (combos + bracket estimate), Archidekt (deck import).

Run: ``uv run mtg-mcp`` (stdio). Registered for Claude Code in .mcp.json.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer

from . import blacklist, brackets, carddb, collection, deckedit, deskmat, edhrec, games, importers, precons, opponents, printorders, proxy, scryfall, spellbook, storage, tablerules
from . import settings as settings_mod
from .cards import resolve
from .deck import DeckEntry, parse_decklist, to_sectioned_text, to_text
from .power import PowerProfile
from .validate import validate_deck as _validate

log = logging.getLogger(__name__)

mcp = MCPServer(
    name="mtg",
    instructions=(
        "Tools for building Magic: The Gathering Commander (EDH) decks. Use the "
        "'commander-deckbuilder' skill for the workflow. Card names are English Oracle names; "
        "the local card DB also resolves names in other languages. Cards on the user's blacklist "
        "(get_blacklist) must never be used; search results already omit them. Always finish a "
        "build with validate_deck and save_deck."
    ),
)

Bracket = Annotated[int, Field(ge=1, le=5, description="Commander Bracket 1-5")]
Currency = Literal["eur", "usd"]


def _slim(card: dict[str, Any]) -> dict[str, Any]:
    """Card data for the model: drop images/links and empty fields to save context."""
    drop = {"image", "image_back", "scryfall_uri", "layout", "tokens"}
    return {k: v for k, v in card.items() if k not in drop and v not in (None, "", [], False)}


# --- card data ----------------------------------------------------------------------------


@mcp.tool()
async def search_cards(
    query: Annotated[str, Field(description="Scryfall search syntax, e.g. 'id<=bg otag:ramp -is:gamechanger usd<5'")],
    max_results: Annotated[int, Field(ge=1, le=100)] = 25,
    order: Literal["edhrec", "cmc", "usd", "eur", "name", "released"] = "edhrec",
) -> dict[str, Any]:
    """Live Scryfall search (https://scryfall.com/docs/syntax). Results sorted by EDHREC popularity by default.

    Useful filters: id<=<colors> (fits color identity), f:commander, otag:<tagger tag> (ramp, draw,
    removal, sweeper, tutor, counterspell ...), is:gamechanger, -is:gamechanger, usd<N / eur<N,
    t:<type>, o:"<oracle text>", mv<=N, is:commander.
    """
    result = await scryfall.search(query, order=order, max_results=max_results)
    cards = blacklist.filter_cards([scryfall.compact(c) for c in result["cards"]])
    return {"total": result["total"], "cards": [_slim(c) for c in cards]}


@mcp.tool()
async def local_card_search(
    color_identity: Annotated[str | None, Field(description="Commander color identity like 'WUB'; '' = colorless only")] = None,
    tags: Annotated[list[str] | None, Field(description="Scryfall Tagger oracle tags, all must match, e.g. ['ramp'], ['removal'], ['sweeper'], ['draw']")] = None,
    text: Annotated[str | None, Field(description="Substring of the oracle text")] = None,
    type_contains: Annotated[str | None, Field(description="Substring of the type line, e.g. 'Creature', 'Equipment'")] = None,
    max_cmc: float | None = None,
    max_price: Annotated[float | None, Field(description="Max price of the cheapest printing")] = None,
    currency: Currency = "eur",
    commanders_only: bool = False,
    exclude_game_changers: bool = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    """Fast offline search in the local card DB (Scryfall bulk data + Tagger tags), sorted by EDHREC rank.

    Only Commander-legal cards. Requires the DB (see card_db_status / update_card_database).
    """
    if not carddb.available():
        return {"error": "Local card DB missing. Call update_card_database first or use search_cards."}
    cards = carddb.search(
        color_identity=color_identity, tags=tags, text=text, type_contains=type_contains, max_cmc=max_cmc,
        max_price=max_price, currency=currency, commanders_only=commanders_only,
        exclude_game_changers=exclude_game_changers, limit=limit,
    )  # fmt: skip
    cards = blacklist.filter_cards(cards)
    return {"count": len(cards), "cards": [_slim(c) for c in cards]}


@mcp.tool()
async def get_cards(
    names: Annotated[list[str], Field(description="Card names (English or other languages), max ~150")],
) -> dict[str, Any]:
    """Look up full card data (oracle text, color identity, price, game changer flag, roles) for card names."""
    cards, renames, missing = await resolve(names)
    return {"cards": [_slim(c) for c in cards.values()], "renamed": renames, "not_found": missing}


@mcp.tool()
async def find_commanders(
    query: Annotated[str, Field(description="Scryfall syntax, e.g. 'id=bg o:sacrifice' or 'id=wubrg'")] = "",
    max_results: Annotated[int, Field(ge=1, le=50)] = 15,
) -> dict[str, Any]:
    """Find legal commanders (legendary creatures etc.), most popular (EDHREC rank) first.

    For open-ended ideas ("I like dragons", "lots of tokens in Selesnya") combine several searches,
    e.g. 'id=wg o:token', 't:dragon', 'id<=br o:sacrifice'. See the commander-finder skill.
    """
    result = await scryfall.search(f"is:commander f:commander {query}".strip(), max_results=max_results)
    cards = blacklist.filter_cards([scryfall.compact(c) for c in result["cards"]])
    return {"total": result["total"], "commanders": [_slim(c) for c in cards]}


@mcp.tool()
async def game_changers() -> dict[str, Any]:
    """The current official Game Changers list (from Scryfall's is:gamechanger)."""
    names = await scryfall.game_changers()
    return {"count": len(names), "game_changers": names}


@mcp.tool()
async def card_db_status() -> dict[str, Any]:
    """Status of the local card database (Scryfall bulk data): size, age, languages."""
    return {**carddb.status(), "needs_refresh": carddb.needs_refresh(), "bulk_type": carddb.BULK_TYPE}


@mcp.tool()
async def update_card_database(force: bool = False) -> dict[str, Any]:
    """Download Scryfall bulk data (cards + oracle tags) into the local DB. Skipped if younger than
    MTG_BULK_MAX_AGE_DAYS unless force=True. The All Cards file is ~375 MB and takes a few minutes."""
    return await carddb.refresh(force=force)


# --- EDHREC -----------------------------------------------------------------------------------


@mcp.tool()
async def edhrec_recommendations(
    commander: str,
    partner: str | None = None,
    theme: Annotated[str | None, Field(description="EDHREC theme slug from the 'themes' list, e.g. 'tokens'")] = None,
    bracket: Annotated[int | None, Field(ge=1, le=5, description="Only decks of this bracket")] = None,
    budget: Literal["budget", "expensive"] | None = None,
    per_category: Annotated[int, Field(ge=5, le=60)] = 20,
) -> dict[str, Any]:
    """EDHREC card recommendations for a commander: categories (High Synergy, Top Cards, Creatures,
    Ramp, Removal ...) with synergy score and inclusion rate, plus available themes.

    If a bracket/theme/budget combination has no page, retry without that filter.
    """
    try:
        page = await edhrec.commander_page(
            [commander, partner or ""], theme=theme, budget=budget, bracket=bracket, per_category=per_category
        )
    except LookupError as exc:
        return {"error": str(exc)}
    for cat in page["categories"]:
        cat["cards"] = blacklist.filter_cards(cat["cards"])
    return page


@mcp.tool()
async def edhrec_average_deck(
    commander: str,
    partner: str | None = None,
    bracket: Annotated[int | None, Field(ge=1, le=5)] = None,
    budget: Literal["budget", "expensive"] | None = None,
) -> dict[str, Any]:
    """EDHREC 'average deck' (100 cards incl. basics) – a good skeleton to start from."""
    try:
        return await edhrec.average_deck([commander, partner or ""], budget=budget, bracket=bracket)
    except LookupError as exc:
        return {"error": str(exc)}


# --- combos & brackets ---------------------------------------------------------------------


@mcp.tool()
async def find_combos(
    commanders: list[str],
    cards: Annotated[list[str], Field(description="Card names of the main deck")],
) -> dict[str, Any]:
    """Commander Spellbook: combos fully in the deck and combos missing one card."""
    return await spellbook.find_combos(commanders, [e.name for e in parse_decklist(cards).entries])


@mcp.tool()
async def bracket_rules() -> dict[str, Any]:
    """Official Commander Bracket rules (1 Exhibition ... 5 cEDH)."""
    return {
        "brackets": brackets.describe(),
        "notes": [
            "Since Oct 2025 there are no tutor limits; efficient tutors are Game Changers.",
            "Game Changer list: call game_changers (live from Scryfall).",
            "Brackets are about expectations: also consider speed, consistency and how the deck wins.",
            "Inside a bracket, a power_profile sets a sub-tier (low/mid/high, e.g. 'upper 3', 'lower 4') and "
            "stricter house rules (max_game_changers, max_tutors, no combos/extra turns/MLD) plus a style. "
            "validate_deck reports a heuristic power score (e.g. 3.8 = upper bracket 3).",
        ],
    }


@mcp.tool()
async def validate_deck(
    commanders: Annotated[list[str], Field(description="1 or 2 commander names")],
    cards: Annotated[list[str], Field(description="Main deck lines without commanders: '1 Sol Ring', '12 Forest'")],
    bracket: Bracket,
    currency: Currency = "eur",
    budget: Annotated[float | None, Field(description="Max. total deck price; exceeding it is a warning")] = None,
    proxy: Annotated[bool, Field(description="Deck will be printed as proxies: prices don't matter")] = False,
    power_profile: Annotated[PowerProfile | None, Field(description="Sub-tier, house rules and style inside the bracket")] = None,
    table_rule: Annotated[str | None, Field(description="Id (or name) of the table rule set the deck must follow, see table_rules")] = None,
) -> dict[str, Any]:
    """Check a deck: 100 cards, singleton, color identity, banned cards, user blacklist, budget,
    land/ramp/draw/removal counts, bracket rules (Game Changers, mass land denial, extra turns,
    2-card combos via Commander Spellbook), power profile house rules, the table rule and a heuristic power score."""
    result = await _validate(
        commanders, cards, bracket, currency=currency, budget=budget, proxy=proxy, profile=power_profile,
        table_rule=table_rule,
    )
    result.pop("_card_data", None)
    result.pop("cards", None)
    result["stats"].pop("roles", None)  # role -> card lists are long; counts suffice here
    return result


# --- decks ------------------------------------------------------------------------------------


class CardEntry(BaseModel):
    name: str
    qty: int = 1
    category: str = Field("", description="Deck role for grouping, e.g. Ramp, Draw, Removal, Board Wipe, Synergy, Win Condition, Land")


SAME_BUILD_MINUTES = 30


def _same_build(existing_slug: str, commanders: list[str]) -> bool:
    """Is the existing deck an earlier save of this very build (validate → fix → save again without slug)?
    In a GUI job: saved by the same job. Otherwise: same commanders and saved within SAME_BUILD_MINUTES."""
    from datetime import datetime, timezone

    try:
        old = storage.load(existing_slug)
    except (FileNotFoundError, storage.StoreError):
        return False
    job = os.environ.get("MTG_JOB_ID")
    if job:
        return old.get("last_job") == job
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(old.get("updated") or "")
    except ValueError:
        return False
    return sorted(old.get("commanders") or []) == sorted(commanders) and age.total_seconds() < SAME_BUILD_MINUTES * 60


@mcp.tool()
async def save_deck(
    name: Annotated[str, Field(description="Deck name")],
    commanders: list[str],
    cards: Annotated[list[CardEntry], Field(description="All 99 (or 98) non-commander cards incl. basic lands")],
    bracket: Bracket,
    description: Annotated[str, Field(description="Short description of game plan and key synergies (user's language)")] = "",
    strategy: str = "",
    budget: float | None = None,
    currency: Currency = "eur",
    proxy: Annotated[bool, Field(description="Deck will be printed as proxies (budget ignored)")] = False,
    power_profile: Annotated[PowerProfile | None, Field(description="Sub-tier, house rules and style inside the bracket")] = None,
    change_note: Annotated[str, Field(description="When updating a deck: what was changed and why (stored in the deck history)")] = "",
    notes: Annotated[str, Field(description="Notes for the player: mulligan tips, combos, upgrade ideas")] = "",
    slug: Annotated[str | None, Field(description="Existing deck slug to overwrite (when refining a deck)")] = None,
    table_rule: Annotated[str | None, Field(description="Table rule set id/name the deck follows; omit to keep the deck's current one, '' for none")] = None,
) -> dict[str, Any]:
    """Validate and save a deck to decks/<slug>.json (+ .txt export for Moxfield/Archidekt). Shown in the GUI."""
    lines = [f"{c.qty} {c.name}" for c in cards]
    if table_rule is None and slug:  # refining: the deck keeps its table rule
        try:
            table_rule = storage.load(slug).get("table_rule")
        except FileNotFoundError:
            table_rule = None
    rs = tablerules.resolve_id(table_rule)
    result = await _validate(
        commanders, lines, bracket, currency=currency, budget=budget, proxy=proxy, profile=power_profile,
        table_rule=rs["id"] if rs else table_rule or None,
    )
    card_data = result.pop("_card_data")
    categories = {c.name: c.category for c in cards}
    renamed = result["renamed"]
    for original, english in renamed.items():
        if original in categories:
            categories[english] = categories.pop(original)

    deck = {
        "name": name,
        "commanders": result["commanders"],
        "bracket": bracket,
        "description": description,
        "strategy": strategy,
        "budget": None if proxy else budget,
        "proxy": proxy,
        "power_profile": power_profile.model_dump(exclude_defaults=True) if power_profile else None,
        "currency": currency,
        "notes": notes,
        "change_note": change_note,
        "table_rule": rs["id"] if rs else None,
        "cards": [{**c, "category": categories.get(c["name"], "")} for c in result.pop("cards")],
        "validation": result,
    }
    if slug:
        deck["slug"] = storage.slug(slug)
    else:  # a new deck never overwrites another deck with the same name …
        deck["slug"] = storage.unique_slug(name)
        own = storage.slug(name)
        if deck["slug"] != own and _same_build(own, result["commanders"]):  # … only its own earlier save
            deck["slug"] = own
    paths = storage.save(deck)
    result["stats"].pop("roles", None)
    return {
        "saved": paths,
        "legal": result["legal"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "bracket": {k: result["bracket"][k] for k in ("target", "target_text", "compliant", "violations", "warnings", "estimated", "game_changers")},
        "power": {k: result["bracket"]["power"][k] for k in ("value", "text", "components")},
        "price_total": result["price_total"],
        "proxy": proxy,
        "table_rule": result.get("table_rule"),
        "card_count": result["stats"]["card_count"] + len(result["commanders"]),
        "unresolved_cards": [n for n in categories if n not in card_data],
        "slug": paths["slug"],
        "hint": (f"Fix errors/violations and call save_deck again with slug='{paths['slug']}'." if not result["legal"]
                 or not result["bracket"]["compliant"] else f"Saved as slug='{paths['slug']}' – pass it for further changes."),
    }


@mcp.tool()
async def edit_deck(
    slug: str,
    add: Annotated[list[CardEntry] | None, Field(description="Cards to add (quantities add up; category optional)")] = None,
    remove: Annotated[list[str] | None, Field(description="Card names to remove completely")] = None,
    change_note: Annotated[str, Field(description="Why (stored in the deck history)")] = "",
) -> dict[str, Any]:
    """Targeted swaps in a saved deck without resending the whole list: adds/removes cards,
    re-validates and saves a new version. Keep the deck at 100 cards (add as many as you remove)."""
    try:
        return await deckedit.edit_deck(
            slug, add=[c.model_dump() for c in add or []], remove=remove or [], note=change_note
        )
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def similar_cards(
    slug: str, card: Annotated[str, Field(description="Card in the deck to replace")], limit: int = 12
) -> list[dict[str, Any]]:
    """Replacement candidates for one card of a saved deck: same role/tags, inside the colour
    identity, not in the deck, not blacklisted, most popular first."""
    return [_slim(c) for c in await deckedit.similar_cards(storage.load(slug), card, limit=limit)]


# --- collection ------------------------------------------------------------------------------


@mcp.tool()
async def collection_search(
    color_identity: Annotated[str | None, Field(description="Commander colour identity, e.g. 'BG' – only cards that fit")] = None,
    text: Annotated[str, Field(description="Substring of the Oracle text")] = "",
    type_contains: Annotated[str, Field(description="Substring of the type line, e.g. 'Creature'")] = "",
    limit: int = 300,
) -> dict[str, Any]:
    """Cards the user owns (their collection), filtered like a card search. real = physical copies,
    proxy = printed proxies. Use when the user wants to build from their collection."""
    cards = await collection.search_owned(color_identity, text=text, type_contains=type_contains, limit=limit)
    return {"collection_size": len(collection.load()), "count": len(cards), "cards": cards}


@mcp.tool()
async def collection_status(slug: str) -> dict[str, Any]:
    """Compare a saved deck with the user's collection: how many cards they own (real/proxy),
    what is missing (shopping list with prices) and cards that several decks share."""
    o = await collection.deck_ownership(storage.load(slug))
    o.pop("cards", None)
    return o


@mcp.tool()
async def update_collection(
    lines: Annotated[str, Field(description="Card list or CSV, e.g. '2 Sol Ring' per line, '*F*' foil, '[proxy]' proxy")],
    proxy: Annotated[bool, Field(description="Mark all as proxies")] = False,
) -> dict[str, Any]:
    """Add cards to the user's collection (only when the user asks for it)."""
    try:
        return await collection.import_text(lines, proxy=proxy)
    except ValueError as exc:
        return {"error": str(exc)}


# --- blacklist --------------------------------------------------------------------------------


@mcp.tool()
async def get_blacklist() -> dict[str, Any]:
    """The user's blacklist: cards and rules (groups/terms like True Duals, cheap tutors, fast mana,
    a price limit, or free-text wishes) that must never go into a deck. Search results already omit
    blacklisted cards and cards breaking a checked rule; free-text rules (checked=false) are yours to respect."""
    names = blacklist.load()
    rules = blacklist.rules()
    return {"count": len(names), "blacklist": names, "rules": rules,
            "note": "Rules with checked=true are enforced by validate_deck; checked=false rules are free-text wishes you must respect yourself."}  # fmt: skip


@mcp.tool()
async def update_blacklist(
    add: Annotated[list[str] | None, Field(description="Card names (any language) or terms like 'True Duals', 'Günstige Tutoren', 'Fast Mana', 'teurer als 20 €'; prefix '@' to store any text as a free-text rule")] = None,
    remove: Annotated[list[str] | None, Field(description="Card names, or rule lines as returned by get_blacklist (e.g. '@cheap-tutors')")] = None,
) -> dict[str, Any]:
    """Add or remove cards and rules on the user's blacklist. Only on explicit user request."""
    return await blacklist.update(add, remove)


# --- table rules ------------------------------------------------------------------------------


@mcp.tool()
async def table_rules() -> dict[str, Any]:
    """The user's table rules ("Tischregeln"): named rule sets for a playgroup (max. bracket, Game
    Changers, tutors, deck budget, no proxies, forbidden card groups and cards, free-text agreements).
    A deck follows at most one (deck field `table_rule`); pass its id to validate_deck/save_deck."""
    sets = tablerules.all_sets()
    return {"count": len(sets), "table_rules": [
        {"id": s["id"], "name": s["name"], "description": s.get("description", ""), "rules": s["summary"]} for s in sets]}  # fmt: skip


@mcp.tool()
async def update_table_rule(
    name: Annotated[str, Field(description="Name of the rule set, e.g. 'Freitagsrunde' (created if it does not exist)")],
    add: Annotated[list[str] | None, Field(description="Forbidden cards or terms: 'True Duals', 'Günstige Tutoren', '2-Karten-Combos', 'teurer als 5 €', '@free-text agreement'")] = None,
    remove: Annotated[list[str] | None, Field(description="Card names or rule lines ('@cheap-tutors') to take out")] = None,
    max_bracket: Annotated[int | None, Field(ge=0, le=5, description="Highest bracket allowed; 0 clears")] = None,
    max_game_changers: Annotated[int | None, Field(ge=-1, description="Max. Game Changers; -1 clears")] = None,
    max_tutors: Annotated[int | None, Field(ge=-1, description="Max. tutors; -1 clears")] = None,
    deck_budget: Annotated[float | None, Field(ge=0, description="Max. deck price (also for proxy decks); 0 clears")] = None,
    no_proxies: bool | None = None,
    description: str | None = None,
    delete: bool = False,
) -> dict[str, Any]:
    """Create, change or delete a table rule set. Only on explicit user request."""
    rs = tablerules.resolve_id(name)
    if delete:
        if not rs:
            return {"error": f"Keine Tischregel „{name}“"}
        tablerules.delete(rs["id"])
        return {"deleted": rs["name"]}
    changes: dict[str, Any] = {}
    if max_bracket is not None:
        changes["max_bracket"] = max_bracket or None
    if max_game_changers is not None:
        changes["max_game_changers"] = None if max_game_changers < 0 else max_game_changers
    if max_tutors is not None:
        changes["max_tutors"] = None if max_tutors < 0 else max_tutors
    if deck_budget is not None:
        changes["deck_budget"] = deck_budget or None
    if no_proxies is not None:
        changes["no_proxies"] = no_proxies
    if description is not None:
        changes["description"] = description
    if rs is None:
        out = await tablerules.create(name, add=add, remove=remove, **changes)
    else:
        out = await tablerules.update(rs["id"], add=add, remove=remove, **changes)
        await tablerules.revalidate_decks(rs["id"])
    return {"id": out["id"], "name": out["name"], "rules": out["summary"], "not_found": out.get("not_found", [])}


# --- opponent decks ---------------------------------------------------------------------------


@mcp.tool()
async def opponent_decks() -> dict[str, Any]:
    """Decks the user played against: commander(s), label/player, rough bracket, traits (tags), the user's
    observations and the record of the user's decks against them. No card lists – look up typical cards of a
    commander with edhrec_recommendations. Use them when tuning a deck for the user's playgroup."""
    items = opponents.all_opponents()
    return {"count": len(items), "opponent_decks": [
        {"id": o["id"], "name": o["title"], "commanders": o["commanders"], "player": o.get("player") or None,
         "bracket": o.get("bracket"), "table_rule": o.get("table_rule"), "traits": o["tag_labels"],
         "notes": [n["text"] for n in (o.get("notes") or [])[-8:]],
         "record": {k: o["record"][k] for k in ("games", "wins", "losses", "draws", "per_deck")}} for o in items],
        "tags": {k: v[0] for k, v in opponents.TAGS.items()}}  # fmt: skip


@mcp.tool()
async def update_opponent_deck(
    commander: Annotated[str, Field(description="Commander of the opponent deck (any language); finds or creates it")],
    note: Annotated[str, Field(description="Observation to add, e.g. 'gewinnt mit Thassa's Oracle', 'viele Board Wipes'")] = "",
    tags: Annotated[list[str] | None, Field(description="Traits, keys from opponent_decks.tags (combo, fast, stax, wipes, …); replaces the list")] = None,
    label: Annotated[str | None, Field(description="Own name, e.g. 'Tims Atraxa'")] = None,
    player: str | None = None,
    bracket: Annotated[int | None, Field(ge=1, le=5)] = None,
    opponent_id: Annotated[str | None, Field(description="Id when several decks share the commander")] = None,
    delete: bool = False,
) -> dict[str, Any]:
    """Create or change an opponent deck, or add an observation. Only on explicit user request."""
    o = opponents.get(opponent_id) if opponent_id else None
    if o is None:
        _, renames, _ = await resolve([commander])
        found = opponents.find(renames.get(commander, commander))
        o = found[0] if len(found) == 1 else None
        if len(found) > 1:
            return {"error": "Mehrere Gegnerdecks mit diesem Commander – gib opponent_id an.",
                    "candidates": [{"id": x["id"], "name": opponents.title(x)} for x in found]}  # fmt: skip
    if delete:
        if not o:
            return {"error": f"Kein Gegnerdeck mit {commander}"}
        opponents.delete(o["id"])
        return {"deleted": opponents.title(o)}
    changes = {k: v for k, v in {"tags": tags, "label": label, "player": player, "bracket": bracket}.items() if v is not None}
    try:
        o = await (opponents.update(o["id"], add_note=note, **changes) if o else opponents.create([commander], note=note, **changes))
    except ValueError as exc:
        return {"error": str(exc)}
    d = opponents.describe(o)
    return {"id": d["id"], "name": d["title"], "traits": d["tag_labels"], "notes": [n["text"] for n in d["notes"][-8:]]}


@mcp.tool()
async def list_decks() -> list[dict[str, Any]]:
    """Saved decks (newest first)."""
    return storage.list_decks()


@mcp.tool()
async def load_deck(slug: str) -> dict[str, Any]:
    """Load a saved deck (cards with categories, description, last validation summary)."""
    deck = storage.load(slug)
    v = deck.get("validation") or {}
    deck["validation"] = {k: v.get(k) for k in ("legal", "errors", "warnings", "bracket", "price_total")}
    rs = tablerules.get(deck.get("table_rule"))
    if rs:
        deck["table_rule_info"] = {"id": rs["id"], "name": rs["name"], "rules": tablerules.summary_lines(rs),
                                   "hint": f"Pass table_rule='{rs['id']}' to validate_deck and save_deck."}  # fmt: skip
    deck["history"] = (deck.get("history") or [])[-5:]  # latest changes are enough for context
    return deck


@mcp.tool()
async def deck_games(slug: str) -> dict[str, Any]:
    """Games the user logged with a saved deck: result, opponents' commanders, end turn, problems
    (e.g. 'zu wenige Länder'), best card, note, deck version – plus win/loss record, most common
    problems and a focus text for upgrades. Use it for questions and upgrades about the deck."""
    try:
        storage.load(slug)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    data = games.summary(slug)
    data["games"] = data["games"][-30:]  # recent games are enough for context
    return data


# --- versions ---------------------------------------------------------------------------------


@mcp.tool()
async def list_deck_versions(slug: str) -> dict[str, Any]:
    """Version history of a saved deck: per version the note, cards added/removed, level
    (e.g. 'oberes Bracket 3'), price and power score. Every save_deck creates a version."""
    try:
        return {"slug": slug, "versions": storage.versions(slug)}
    except FileNotFoundError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def compare_deck_versions(
    slug: str,
    from_version: int,
    to_version: Annotated[int | None, Field(description="Default: current version")] = None,
) -> dict[str, Any]:
    """Diff between two versions of a deck: cards in/out, level, price and power changes."""
    try:
        return storage.compare(slug, from_version, to_version)
    except FileNotFoundError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def restore_deck_version(slug: str, version: int, note: str = "") -> dict[str, Any]:
    """Make an old version current again (saved as a new version, nothing is lost).
    Use for 'undo', 'go back to the version before ...'. Only on user request."""
    try:
        saved = storage.restore(slug, version, note)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    return {"restored": version, **saved, "diff_to_before": storage.compare(slug, saved["version"] - 1)}


@mcp.tool()
async def copy_deck(
    slug: str,
    new_name: Annotated[str | None, Field(description="Name of the copy; default '<name> (Kopie)'")] = None,
    version: Annotated[int | None, Field(description="Copy this old version instead of the current one")] = None,
) -> dict[str, Any]:
    """Duplicate a deck as a new, independent deck – e.g. to build a bracket 2 and a bracket 4
    variant of the same commander. Only on user request."""
    try:
        return storage.copy(slug, new_name, version)
    except FileNotFoundError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def export_deck(
    slug: str, format: Literal["moxfield", "sectioned"] = "moxfield", version: int | None = None
) -> str:
    """Deck (or an old version of it) as text for import in Moxfield/Archidekt/ManaBox
    ('moxfield': commander marked *CMDR*)."""
    deck = storage.load_version(slug, version)
    entries = [DeckEntry(c["name"], c.get("qty", 1)) for c in deck["cards"]]
    return (to_text if format == "moxfield" else to_sectioned_text)(deck["commanders"], entries)


# --- proxies (MPC Autofill) ---------------------------------------------------------------------


@mcp.tool()
async def create_proxy_order(
    slug: str,
    source: Annotated[Literal["auto", "mpcfill", "scryfall"], Field(description="auto = own picks > MPC Autofill scans (if a server is set) > Scryfall")] = "auto",
    stock: Annotated[str | None, Field(description="MPC cardstock, e.g. '(S30) Standard Smooth', '(S33) Superior Smooth', '(M31) Linen'")] = None,
    foil: bool | None = None,
    version: Annotated[int | None, Field(description="Print an old version of the deck")] = None,
    upscale: Annotated[bool | None, Field(description="Opt-in: AI-upscale Scryfall scans to 600 DPI with Real-ESRGAN (only if the user asks; default = setting, off)")] = None,
) -> dict[str, Any]:
    """Prepare proxy printing: download + process all card images (bleed, double-faced backs,
    cardback) and write the MPC Autofill order to proxies/<slug>/. Afterwards: export_proxy_pdf
    (home printing) or launch_proxy_tool (upload to MakePlayingCards)."""
    try:
        deck = storage.load_version(slug, version)
        deck["slug"] = storage.slug(slug)
        return await proxy.prepare(deck, source=source, stock=stock, foil=foil, upscale=upscale)
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def export_proxy_pdf(
    slug: str,
    paper: Literal["A4", "Letter"] = "A4",
    include_backs: Annotated[bool, Field(description="Also print back faces of double-faced cards")] = True,
) -> dict[str, Any]:
    """PDF for home printing (3 x 3 cards per page, 63 x 88 mm, cut marks) from the prepared order."""
    try:
        return await asyncio.to_thread(proxy.export_pdf, storage.slug(slug), paper=paper, include_backs=include_backs)
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def launch_proxy_tool(
    slug: str,
    mode: Annotated[Literal["mpc", "pdf"], Field(description="mpc = fill a MakePlayingCards project, pdf = the tool's PDF export")] = "mpc",
) -> dict[str, Any]:
    """Start the MPC Autofill desktop tool on the prepared order (opens a console + browser on the
    user's machine for the MakePlayingCards login/upload). Only on explicit user request."""
    try:
        return proxy.launch_autofill(proxy.order_dir(storage.slug(slug)), mode=mode)
    except (FileNotFoundError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def proxy_settings(
    autofill_path: Annotated[str | None, Field(description="Path to the MPC Autofill desktop tool")] = None,
    mpcfill_server: Annotated[str | None, Field(description="MPC Autofill search server URL")] = None,
    cardback_path: Annotated[str | None, Field(description="Own cardback image file")] = None,
    browser: Literal["chrome", "edge", "brave"] | None = None,
    stock: str | None = None,
    upscale: Annotated[bool | None, Field(description="Default for AI upscaling (opt-in)")] = None,
    upscaler_path: Annotated[str | None, Field(description="Path to realesrgan-ncnn-vulkan")] = None,
) -> dict[str, Any]:
    """Show (no arguments) or change the proxy printing settings."""
    changes = {k: v for k, v in locals().items() if v is not None}
    cfg = settings_mod.update(changes) if changes else settings_mod.load()
    found, upscaler = proxy.find_autofill(cfg), proxy.find_upscaler(cfg)
    return {**cfg, "autofill_found": str(found) if found else None,
            "upscaler_found": str(upscaler) if upscaler else None,
            "upscale_models": proxy.upscale_models(upscaler), "stocks": proxy.STOCKS}  # fmt: skip


@mcp.tool()
async def print_orders() -> dict[str, Any]:
    """Collective print orders (Sammelbestellungen): name, id, counts and positions (cards/tokens with source)."""
    return {"orders": printorders.orders()}


class OrderPos(BaseModel):
    name: str
    qty: int = 1


@mcp.tool()
async def update_print_order(
    order: Annotated[str | None, Field(description="Id or exact name of an order; a new order with this name is created if none matches")] = None,
    add_cards: Annotated[list[OrderPos] | None, Field(description="Cards to print (any language, resolved to Oracle names)")] = None,
    add_tokens: Annotated[list[OrderPos] | None, Field(description="Tokens/emblems by name, e.g. Treasure x 50")] = None,
    from_deck: Annotated[str | None, Field(description="Add the cards of this saved deck (slug)")] = None,
    only_missing: Annotated[bool, Field(description="With from_deck: only cards the collection lacks")] = False,
    source: Annotated[str, Field(description="Label of the added positions, e.g. 'Upgrades Aesi'")] = "",
) -> dict[str, Any]:
    """Add cards and tokens to a collective print order, printed together like one deck in the GUI
    (MPC Autofill / PDF). Returns the order with counts."""
    existing = next((o for o in printorders.orders() if order and (o["id"] == order or o["name"].lower() == order.lower())), None)
    target = existing or printorders.create(order or "Sammelbestellung")
    items = [{"kind": "card", "name": c.name, "qty": c.qty, "source": source or "Claude"} for c in add_cards or []]
    items += [{"kind": "token", "name": t.name, "qty": t.qty, "source": source or "Tokens"} for t in add_tokens or []]
    try:
        if from_deck:
            items += printorders.deck_items(storage.load(from_deck), only_missing=only_missing)
        if not items:
            return {"error": "Nichts hinzuzufügen.", "order": target}
        return await printorders.add(target["id"], items)
    except (ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def create_deskmat(
    card: Annotated[str | None, Field(description="Card whose artwork becomes the mat (Scryfall art crop)")] = None,
    image_prompt: Annotated[str | None, Field(description="Or: English text-to-image prompt (wide panorama, no text/frame) for the free generator")] = None,
    format: Annotated[str, Field(description="playmat (61x35.5 cm) | deskmat-80x30 | deskmat-90x40 | deskmat-120x60")] = "playmat",
    dpi: Annotated[int, Field(description="Print resolution: 300 (minimum) or 600 (best)")] = 300,
    bleed_mm: Annotated[float, Field(description="Bleed on every side in mm: 0, 3 or 5 (if the print shop asks for it)")] = 0,
    fit: Annotated[str, Field(description="fill = crop to the format, fit = whole image with blurred edges")] = "fill",
    passes: Annotated[int, Field(description="Real-ESRGAN passes: 1 (more natural, default) or 2 (sharper, can look artificial)")] = 1,
) -> dict[str, Any]:
    """Create a deskmat/playmat print file at 300 or 600 DPI (Real-ESRGAN upscaling when set up) in deskmats/<id>/.
    Give either ``card`` or ``image_prompt``. Returns the file path, size and warnings."""
    if bool(card) == bool(image_prompt):
        return {"error": "Entweder card oder image_prompt angeben."}
    try:
        deskmat.check_size(format, dpi, bleed_mm)
    except ValueError as exc:
        return {"error": f"{exc} (format: {', '.join(deskmat.FORMATS)})"}
    try:
        if card:
            project = await deskmat.from_card(card)
        else:
            project = await deskmat.generate(image_prompt[:60], image_prompt, format, variants=1)  # type: ignore[index]
            project = deskmat.choose(project["id"], 0)
        project = await deskmat.render(project["id"], fmt=format, dpi=dpi, bleed_mm=bleed_mm, fit=fit, passes=min(max(passes, 1), 2))
    except Exception as exc:
        return {"error": str(exc)}
    res = project["result"]
    return {"id": project["id"], "file": str(deskmat.file(project["id"], "result")), "size": res["size"], "dpi": res["dpi"],
            "print_mm": res["print_mm"], "ai_passes": res["ai_passes"], "warnings": res["warnings"]}  # fmt: skip


@mcp.tool()
async def search_precons(query: Annotated[str, Field(description="Words in the name, set code or year, e.g. 'lorehold' or '2024'")] = "") -> dict[str, Any]:
    """Preconstructed Commander decks (starter decks) from MTGJSON, newest first: file, name, code, release date."""
    try:
        return {"precons": await precons.search(query, limit=40)}
    except Exception as exc:
        return {"error": f"MTGJSON nicht erreichbar: {exc}"}


@mcp.tool()
async def import_precon(
    file: Annotated[str, Field(description="MTGJSON file name from search_precons")],
    bracket: Bracket = 2,
) -> dict[str, Any]:
    """Save a precon as a new deck (validated, categories guessed). Returns the saved paths incl. slug."""
    try:
        return await precons.import_precon(file, bracket=bracket)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"MTGJSON nicht erreichbar: {exc}"}


@mcp.tool()
async def import_deck(
    url: Annotated[str, Field(description="Deck URL: Archidekt, Moxfield, MTGGoldfish, TappedOut, Deckstats or an EDHREC average deck")],
) -> dict[str, Any]:
    """Import a public deck from a deckbuilding site: name, commanders, cards ("N Name"), categories.
    Moxfield is best effort (no public API); if blocked, ask the user to paste the text export.
    ``commanders`` can be empty when the site does not mark them (``commander_hint`` may help)."""
    try:
        return await importers.import_url(url)
    except Exception as exc:
        return {"error": str(exc)}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
