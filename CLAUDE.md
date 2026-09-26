# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Magic: The Gathering Commander (EDH) deckbuilder driven by Claude Code. The user asks for a deck and Claude uses the `commander-deckbuilder` skill (`.claude/skills/commander-deckbuilder/`) plus the `mcp__mtg__*` tools. The tools come from the `mtg` MCP server in this repo, registered in `.mcp.json`. There is also a web GUI that runs the same skill and MCP server headlessly through the Claude Agent SDK.

When the user asks for a Commander deck, use the `commander-deckbuilder` skill and the `mcp__mtg__*` tools. Answer in German unless the user writes in another language.

## Commands

```bash
uv sync --all-extras                               # install (extras: gui, dev)
uv run pytest                                      # all tests (offline, <2 s)
uv run pytest tests/test_validate.py::test_game_changer_limits   # single test
uv run mtg-mcp                                     # MCP server on stdio (normally started by Claude Code via .mcp.json)
uv run mtg-gui                                     # web GUI on http://127.0.0.1:8765
```

No linter or formatter is configured. Code uses `# fmt: skip` on some dense literals, so black/ruff-format style is expected.

## Architecture

**Layers in `src/mtgdeck/`:**
- **`http.py`**
  - The single shared `httpx.AsyncClient` for every data source.
  - Rate limits are per host, or per host plus path prefix (Scryfall search, named and collection lookups: 500 ms; everything else: 100 ms).
  - Disk cache keyed by the request, 24 h TTL by default.
  - Always go through `get_json` / `post_json`. Never create another client.
- **Data sources:** `scryfall.py`, `edhrec.py`, `spellbook.py`, `importers.py`.
  - None of them needs an API key.
  - EDHREC is its public JSON (`json.edhrec.com`), which is unofficial, so parse it defensively. A missing EDHREC page returns 403, not 404.
  - EDHREC page paths are `<commander-slug>[/<theme>][/<bracket-slug>][/budget|expensive]`.
- **`scryfall.compact()`**
  - The canonical card dict used everywhere: the DB, validation, MCP output and the GUI.
  - `mcp_server._slim()` strips images and links from it before sending it to the model.
- **`carddb.py`**: a local SQLite DB (`~/.cache/mtgdeck/cards.sqlite`) built from Scryfall bulk data.
  - `MTG_BULK_TYPE` selects the cards file; the default is `all_cards`.
  - The cards file is aggregated to **one row per oracle_id**:
    - the representative printing is chosen by `_printing_score`: English, paper, non-promo;
    - the price is the cheapest paper printing;
    - every printed/face name in every language goes into the `names` table, so German names resolve.
  - The Oracle Tags file (Tagger) goes into `card_tags`. Tags are **rolled up to all ancestors** via `parent_ids`, and aliases live in `tag_aliases`.
- **`cards.resolve()`** is the one entry point for turning names into card data.
  - It tries the local DB first, then falls back to Scryfall `/cards/collection`, then fuzzy `/cards/named`.
  - It returns `renames` (query → English name). Callers must apply these renames to decklists.
- **`deck.py`**
  - Decklist parsing: plain text, Moxfield/Archidekt exports and `*CMDR*` markers.
  - Role detection: Tagger tags when a card has any (`ROLE_TAGS`), otherwise oracle-text regexes.
  - Stats and text export.
- **`brackets.py`**
  - The official bracket rules table (state: WotC Oct 21 2025 rework plus Feb 9 2026 Game Changer update).
  - `evaluate()` combines three inputs:
    - the Scryfall `game_changer` flag (the Game Changer list is never hardcoded);
    - a mass-land-denial list and regex;
    - Commander Spellbook's `/estimate-bracket` and 2-card combos.
- **`validate.py`**
  - `validate_deck()` orchestrates everything above: 100 cards, singleton, color identity, banned cards, role minimums and the bracket check.
  - It returns a private `_card_data` key that callers must pop before serializing or returning it.
  - A Spellbook outage degrades to a warning and does not fail validation.
- **`storage.py`**: saves decks to `decks/<slug>.json` plus a `.txt` export (Moxfield format). The `decks/` contents are gitignored.
- **`mcp_server.py`**: the MCP server, built with `mcp` **2.x**.
  - Use `from mcp.server.mcpserver import MCPServer`. `FastMCP` no longer exists in 2.x.
  - Tools are thin wrappers over the modules above.
  - `save_deck` re-validates before writing and stores the validation result inside the deck JSON.
- **`gui/app.py`**: a FastAPI app with vanilla JS in `gui/static/`.
  - Each build or refine request becomes a `Job` that runs `claude_agent_sdk.query()`.
  - The job uses `cwd=`repo root and `setting_sources=["project"]` so it gets the skills and this file.
  - It starts the MCP server via `sys.executable -m mtgdeck.mcp_server`, with `permission_mode="dontAsk"`.
  - Events stream to the browser over SSE, with `Last-Event-ID` resume.
  - The finished deck is detected as the deck whose `updated` timestamp is ≥ the job start.
  - The prompts in `build_prompt` / `refine_prompt` tell the agent it runs non-interactively.

**Skill ↔ tools contract:**
- `SKILL.md` requires every build to end with `validate_deck` → fix → `save_deck`.
- It also requires `category` values from the fixed set in `references/deck-template.md`, because the GUI groups cards by them.
- If you change tool names or parameters, update `SKILL.md` and the GUI prompts too.

## Tests

- Tests must never hit the network.
- `tests/conftest.py` sets up an autouse fixture that:
  - replaces `http._client` with an `httpx.MockTransport` that fakes Scryfall, EDHREC and Spellbook;
  - redirects the cache, DB and decks directories to `tmp_path`;
  - disables throttling.
- Any card name starting with `Filler` is synthesized on demand. `deck_lines()` builds a legal 99-card main deck for Meren (BG).
- Add new endpoints to the mock `handler`.
- Async tests run with `asyncio_mode = "auto"`.

## Configuration

Environment variables (see README for the full table):
- `MTG_BULK_TYPE`, `MTG_BULK_MAX_AGE_DAYS`
- `MTG_DATA_DIR`, `MTG_CACHE_DIR`, `MTG_CACHE_TTL`
- `MTG_DECKS_DIR`
- `MTG_GUI_HOST`, `MTG_GUI_PORT`, `MTG_MAX_TURNS`
