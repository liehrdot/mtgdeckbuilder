"""MCP server 'mtg': Commander deckbuilding tools for Claude Code.

Data sources (all free, no API keys): Scryfall (API + bulk data), EDHREC (public JSON),
Commander Spellbook (combos + bracket estimate), Archidekt (deck import).

Run: ``uv run mtg-mcp`` (stdio). Registered for Claude Code in .mcp.json.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer

from . import blacklist, brackets, carddb, edhrec, importers, scryfall, spellbook, storage
from .cards import resolve
from .deck import DeckEntry, parse_decklist, to_sectioned_text, to_text
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
    drop = {"image", "scryfall_uri"}
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
) -> dict[str, Any]:
    """Check a deck: 100 cards, singleton, color identity, banned cards, user blacklist, budget,
    land/ramp/draw/removal counts, bracket rules (Game Changers, mass land denial, extra turns,
    2-card combos via Commander Spellbook)."""
    result = await _validate(commanders, cards, bracket, currency=currency, budget=budget, proxy=proxy)
    result.pop("_card_data", None)
    result.pop("cards", None)
    result["stats"].pop("roles", None)  # role -> card lists are long; counts suffice here
    return result


# --- decks ------------------------------------------------------------------------------------


class CardEntry(BaseModel):
    name: str
    qty: int = 1
    category: str = Field("", description="Deck role for grouping, e.g. Ramp, Draw, Removal, Board Wipe, Synergy, Win Condition, Land")


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
    notes: Annotated[str, Field(description="Notes for the player: mulligan tips, combos, upgrade ideas")] = "",
    slug: Annotated[str | None, Field(description="Existing deck slug to overwrite (when refining a deck)")] = None,
) -> dict[str, Any]:
    """Validate and save a deck to decks/<slug>.json (+ .txt export for Moxfield/Archidekt). Shown in the GUI."""
    lines = [f"{c.qty} {c.name}" for c in cards]
    result = await _validate(commanders, lines, bracket, currency=currency, budget=budget, proxy=proxy)
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
        "currency": currency,
        "notes": notes,
        "cards": [{**c, "category": categories.get(c["name"], "")} for c in result.pop("cards")],
        "validation": result,
    }
    if slug:
        deck["slug"] = storage.slug(slug)
    paths = storage.save(deck)
    result["stats"].pop("roles", None)
    return {
        "saved": paths,
        "legal": result["legal"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "bracket": {k: result["bracket"][k] for k in ("target", "compliant", "violations", "warnings", "estimated", "game_changers")},
        "price_total": result["price_total"],
        "proxy": proxy,
        "card_count": result["stats"]["card_count"] + len(result["commanders"]),
        "unresolved_cards": [n for n in categories if n not in card_data],
        "hint": "Fix errors/violations and call save_deck again with the same slug." if not result["legal"] or not result["bracket"]["compliant"] else "",
    }


# --- blacklist --------------------------------------------------------------------------------


@mcp.tool()
async def get_blacklist() -> dict[str, Any]:
    """The user's blacklist: cards that must never be put into a deck."""
    names = blacklist.load()
    return {"count": len(names), "blacklist": names}


@mcp.tool()
async def update_blacklist(
    add: Annotated[list[str] | None, Field(description="Card names to blacklist (any language)")] = None,
    remove: Annotated[list[str] | None, Field(description="Card names to take off the blacklist")] = None,
) -> dict[str, Any]:
    """Add or remove cards on the user's blacklist. Only on explicit user request."""
    return await blacklist.update(add, remove)


@mcp.tool()
async def list_decks() -> list[dict[str, Any]]:
    """Saved decks (newest first)."""
    return storage.list_decks()


@mcp.tool()
async def load_deck(slug: str) -> dict[str, Any]:
    """Load a saved deck (cards with categories, description, last validation summary)."""
    deck = storage.load(slug)
    v = deck.get("validation") or {}
    deck["validation"] = {k: v.get(k) for k in ("legal", "errors", "warnings", "bracket")}
    return deck


@mcp.tool()
async def export_deck(slug: str, format: Literal["moxfield", "sectioned"] = "moxfield") -> str:
    """Deck as text for import in Moxfield/Archidekt/ManaBox ('moxfield': commander marked *CMDR*)."""
    deck = storage.load(slug)
    entries = [DeckEntry(c["name"], c.get("qty", 1)) for c in deck["cards"]]
    return (to_text if format == "moxfield" else to_sectioned_text)(deck["commanders"], entries)


@mcp.tool()
async def import_deck(url: Annotated[str, Field(description="Archidekt or Moxfield deck URL")]) -> dict[str, Any]:
    """Import a public deck from Archidekt (reliable) or Moxfield (best effort, no public API)."""
    try:
        return await importers.import_url(url)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
