# MTG Commander Deckbuilder

This repo is a Commander (EDH) deckbuilder for Claude Code:

- `src/mtgdeck/` – Python package
  - `mcp_server.py` – MCP server `mtg` (registered in `.mcp.json`, run via `uv run mtg-mcp`)
  - `scryfall.py`, `edhrec.py`, `spellbook.py`, `importers.py` – data sources (no API keys)
  - `carddb.py` – local SQLite card DB from Scryfall bulk data (All Cards + Oracle Tags)
  - `validate.py`, `brackets.py`, `deck.py` – legality, bracket rules, stats
  - `storage.py` – saved decks in `decks/<slug>.json` + `.txt`
  - `gui/` – FastAPI web GUI that runs Claude Code via the Claude Agent SDK
- `.claude/skills/commander-deckbuilder/` – the deckbuilding workflow

When the user asks for a Commander deck, use the `commander-deckbuilder` skill and the `mcp__mtg__*` tools.
Answer in German unless the user writes in another language.

Dev: `uv sync --all-extras`, tests `uv run pytest`. Tests must not hit the network (mock `mtgdeck.http`).
