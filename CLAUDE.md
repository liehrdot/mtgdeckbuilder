# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Magic: The Gathering Commander (EDH) deckbuilder driven by Claude Code. The user asks for a deck and Claude uses the `commander-deckbuilder` skill (`.claude/skills/commander-deckbuilder/`) plus the `mcp__mtg__*` tools. Without a commander idea, the `commander-finder` skill suggests commanders first. The tools come from the `mtg` MCP server in this repo, registered in `.mcp.json`. There is also a web GUI that runs the same skill and MCP server headlessly through the Claude Agent SDK.

When the user asks for a Commander deck, use the `commander-deckbuilder` skill and the `mcp__mtg__*` tools. When they have no commander yet, use `commander-finder`. Answer in German unless the user writes in another language.

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
  - Always go through `get_json` / `get_text` / `post_json`. Never create another client.
- **Data sources:** `scryfall.py`, `edhrec.py`, `spellbook.py`, `importers.py`, `precons.py`.
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
  - `SCHEMA_VERSION` (meta `schema_version`) marks which compact fields exist. v2 adds `layout` / `image_back` for double-faced cards, v3 `tokens` (from `all_parts`: tokens, emblems, markers like The Monarch). An older DB reports `schema_outdated` and `needs_refresh()`; `cards.deck_tokens()` completes missing `tokens` live.
  - Meanwhile `cards._complete_double_faced()` fetches `layout` / `image_back` live for multi-face cards that lack them.
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
- **Import:** `importers.py` turns a link (Archidekt incl. categories, Moxfield v3→v2 best effort, MTGGoldfish/TappedOut/Deckstats text exports, EDHREC average decks) or pasted text into one shape `{source, site, name, commanders, cards: ["N Name"], categories, commander_hint}`; `map_category()` maps site categories to ours. `deckimport.preview()` resolves names, offers `commander_candidates` and `suggested` (hint/slug match); `deckimport.save()` validates (bracket `None` = Spellbook/heuristic estimate), fills categories and saves a new deck. Routes `/api/import/preview`, `/api/import`. `precons.py` reads MTGJSON `DeckList.json` + `decks/<file>.json` (cached, defensive) and imports via `deckimport.save` (`deck["precon"]`). None of these sites was reachable from the dev container; formats follow the public endpoints and are covered by mocks.
- **Beginner helpers:**
  - `glossary.py`: `TERMS` (English term → German name, one-line explanation, kind keyword/action/concept), `find_terms()` for card texts. `cards.card_text(name, lang)` returns per-face Oracle text, the newest printing in `lang` (`printed`) and the terms (route `/api/cards/text`, `/api/glossary`).
  - `health.py`: `check(deck)` → traffic light per area from the stored validation stats (no network); returned by `GET /api/decks/{slug}` as `health`. `deckedit.role_candidates()` + `/role-candidates?role=` fix a role without AI.
  - `rule0.py`: `build(deck)` → Rule-0 rows + copyable text (route `/rule0`).
  - Guide: `guide_prompt` / `GUIDE_SCHEMA` / `_clean_guide`, stored as `deck["guide"]` via `storage.set_extra()` (no new version; keeps the version it was written for).
  - `games.py`: game log in `decks/.games/<slug>.json`, `stats()`, `learn_focus()`, `ISSUES` quick picks; routes `/api/decks/{slug}/games`, MCP `deck_games` (read-only, also in `READ_ONLY_TOOLS`); the upgrade prompt mentions the record.
  - Staged upgrade plan: `PLAN_SCHEMA` / `plan_prompt` / `_enrich_plan` (each stage validated like upgrades, no card added/removed twice), stored as `deck["upgrade_plan"]`; the GUI applies one stage at a time via `POST /cards`.
  - Guided finder: `FinderRequest` takes `feel`/`colors`/`themes`/`experience` (`FINDER_*` wordings); suggestions carry `difficulty` + `difficulty_note`.
- **`printorders.py`** (Sammelbestellung): collective print orders in `<proxies>/.orders/<id>.json` with items `{id, kind card|token, name, qty, source, source_slug, type_line, token_id, image}` (equal card+source entries add up). `as_deck()` gives the print pipeline a deck-shaped dict with slug `sammel-<id>` and `print_tokens` (own quantities); `proxy.plan()` plans them via `_plan_tokens()` (MPC TOKEN search, else Scryfall; typed names without image get `_token_image()`). The deck print routes accept order slugs (`_print_deck()`), so plan/prepare/PDF/autofill/add-printed work unchanged. `deck_items()` (all / names / only_missing), `added_since(slug, version)` (cards a rebuild brought in). Routes `/api/orders[/{id}[/items[/{item}]]]` (add: items, deck+names/only_missing/since_version, text, url), `/api/decks/{slug}/added?since=`, `/api/tokens/search`; MCP `print_orders` (read) and `update_print_order` (write).
- **`deskmat.py`** (Deskmat-Studio): projects in `deskmats/<id>/` (`MTG_DESKMAT_DIR`, gitignored) with `meta.json`, `source.*`, `candidates/`, `deskmat-<W>x<H>.png`, `preview.jpg`.
  - Sources: `from_card()` (Scryfall `art_crop` via `scryfall.image_url(id, "art_crop")` or derived from the image URL; `mpc_id` = MPC Autofill full scan with `ART_BOX` as default crop), `from_upload()`, `generate()` (URL template setting `image_generator_url`, default pollinations.ai, throttled, 1–4 seeds) + `choose()`.
  - Print output: `FORMATS` (mm), `DPIS` 300/600, `BLEEDS` 0/3/5 mm, `target_size(fmt, dpi, bleed_mm)`, `check_size()` (cap `MAX_MEGAPIXELS` 250); `crop_box()` (the GUI mirrors it in `dmCropBox`/`dmTarget`). `render()`: fill = crop window (cx, cy, zoom) / fit = blurred extension → `upscale_to()`: descreen scans → Real-ESRGAN ×4 via `proxy._upscale(timeout=)`, with `passes=2` (default 1) a second pass for factors > 4.5 that starts from a quarter of the target → Lanczos + unsharp to the exact size; saves PNG/JPEG with DPI metadata (`deskmat-<W>x<H>-<dpi>dpi.<ext>`).
  - Checking before ordering: `compare(pid, x, y)` renders one `COMPARE_PX` tile of the print without AI / 1 / 2 passes from the source piece only (`_mapping()` print→source, `compare/<n>.png`); `testprint()` puts a real-size piece (`TEST_MM`) on a landscape A4/Letter PDF (setting `paper`) with crop marks. Routes `/compare` (runner job, SSE `deskmat` with `what=compare`), `/compare/{n}`, `/testprint?x=&y=`.
  - Routes `/api/deskmat…` (options, card, mpc, upload, generate = read-only Claude job with `DESKMAT_SCHEMA` then generator, choose, render = runner job, image, open-folder, delete); MCP `create_deskmat` (write tool). SSE event `deskmat`.
- **`deckedit.py`**: `edit_deck()` changes a saved deck directly (add/remove/set_qty/set_category), re-validates via `revalidate()` and saves one version with a "Manuell: …" note (route `POST /api/decks/{slug}/cards`, MCP `edit_deck`). `similar_cards()` suggests replacements (local DB tags, else Scryfall `otag:` search).
- **`collection.py`**: the user's collection in `collection.json` (`MTG_COLLECTION_FILE`, gitignored).
  - One entry per printing: name, qty, proxy, foil, lang, `set`/`set_name`/`collector_number`/`scryfall_id`/`image` (artwork), price at import, note. Identical entries are merged.
  - `parse_import()` reads ManaBox/Moxfield/Archidekt CSV (column aliases in `_COLS`) and plain lists. Printings resolve via `scryfall.by_identifiers()` (id or set+number); a picked printing (`printing` dict) is stored as is.
  - `deck_ownership()` (real/proxy/missing, shopping list, `shared_shortages` across decks; basics count as owned), `missing_counts()` for printing only missing cards, `add_printed()` after printing, `search_owned()` for building from the collection.
  - Routes `/api/collection[...]`, `/api/cards/prints`, `/api/decks/{slug}/ownership`, `/collection/add-printed`; MCP `collection_search`, `collection_status`, `update_collection`.
- **`exports.py`**: Cockatrice `.cod` (commander in the side zone, DFCs by front face) and Tabletop Simulator saved objects; route `/api/decks/{slug}/export/{text|cockatrice|tts}`.
- **`blacklist.py`**: the user's blacklist in `blacklist.txt` (plain text, gitignored, `MTG_BLACKLIST_FILE`): cards and rules.
  - Card lines are Oracle names (resolved on add). Rule lines start with `@`: `@<key>` from `RULES` (curated name lists like `true-duals`/`fetchlands`/`shocklands`/`stax`, predicates on compact card data like `cheap-tutors`/`tutors`/`fast-mana`/`game-changers`/`extra-turns`/`mld`/`free-spells`/`counterspells`/`board-wipes`, deck-level `two-card-combos`), `@price>N[usd]`, `@text:<free text>` (unchecked, for Claude).
  - `parse_rule()` maps typed German/English terms via normalised aliases; `update()` turns known terms into rules, the rest into cards; a leading `@` forces a (free-text) rule. `load()` returns card names only, `rules()` the described rules, `catalog()` the quick-add list.
  - `validate_deck` reports blacklisted cards and `card_rules()` hits as errors ("Blacklist-Regel „…“"), and 2-card Spellbook combos when `two-card-combos` is set.
  - The MCP search tools (`search_cards`, `local_card_search`, `find_commanders`, `edhrec_recommendations`) and `deckedit` suggestions filter them out via `filter_cards()` / `card_rules()`; name-only EDHREC entries are looked up in the local DB for predicate rules.
  - `GET /api/blacklist` → `{cards, rules, catalog}`.
- **`opponents.py`** (Gegnerdecks): decks the user played against in `decks/.opponents.json` – `commanders`, `label`, `player`, `bracket`, `table_rule`, `tags` (keys of `TAGS`: label + what helps against it), `notes` `[{id, text, at, game_id, deck_slug}]`, `color_identity`, `image`. No card lists.
  - Games link to them: `games.add(opponent_ids=)` (same order as `opponents`); `link_game()` matches by id, else by commander, else creates one (`remember`), and stores the per-opponent note. `POST /games` takes `opponent_ids`, `opponent_notes`, `remember_opponents`.
  - `record()` = games against it over all decks (`opponent_ids`, or the commander name for unlinked games); `describe()` adds `title`, `tag_labels`, `record`, `edhrec_url`.
  - `relevant(deck)` picks the opponents that matter (faced with the deck ×3, same table rule +5, else the most played); `prompt_lines(deck, focus_id=)` goes into the refine/retune/upgrade/plan/ask prompts and into builds with a table rule; `UpgradeRequest.opponent_id` = "besser gegen".
  - MCP `opponent_decks` (read-only) and `update_opponent_deck` (write). Routes `/api/opponents[/{id}]`; GUI page `#/opponents[/<id>]`, quick picks + note fields in the game form, select in the upgrade form.
- **`tablerules.py`** (Tischregeln): named rule sets per playgroup in `tablerules.json` (`MTG_TABLERULES_FILE`, gitignored): `rules` (blacklist rule lines), `cards`, `max_bracket`, `max_game_changers`, `max_tutors`, `deck_budget` (+ `currency`, counts for proxy decks too), `no_proxies`.
  - A deck stores one id as `deck["table_rule"]` (a content key, so changing it is a new version). `validate_deck(table_rule=)` adds `violations()` to `errors` and returns `validation["table_rule"]` (`{id, name, compliant, violations, warnings, notes, summary}`); `revalidate()` passes the deck's rule. Power-profile house rules stay separate; both are checked, so the stricter wins.
  - MCP `save_deck(table_rule=)`: omitted keeps the deck's current rule on a refine, `""` removes it. `load_deck` adds `table_rule_info`. Tools `table_rules` (read-only) and `update_table_rule` (write).
  - `check_saved()` / `check_all()` check saved decks without re-validating ("Welche Decks passen?"); `revalidate_decks()` runs after a rule set changes. `prompt_lines()` goes into the build/refine/retune/upgrade/plan/ask prompts; a build job sets the rule afterwards if Claude forgot (`_set_table_rule`).
  - Health has a `table_rule` item, Rule 0 a „Tischregel“ row. Routes `/api/tablerules[/{id}[/decks]]`, `PUT /api/decks/{slug}/table-rule`; GUI page `#/tables[/<id>]`, select in the build form and the „Anpassen“ tab.
- **`power.py`**: sub-tiers inside a bracket.
  - `PowerProfile` (pydantic) holds: tier low/mid/high, stricter house rules (`max_game_changers`, `max_tutors`, `allow_*`), `style` and `notes`.
  - `score()` is a transparent heuristic on the 1.0–5.99 bracket scale (3.8 = upper bracket 3). Hard rules set a floor.
  - `check_profile()` turns house rules into violations. It only warns when a rule is looser than the bracket or the score misses the tier.
  - `brackets.evaluate(profile=)` adds `power`, `profile` and `target_text` to the bracket result.
  - Stored as `deck["power_profile"]`.
- **Budget / proxy:** `validate_deck(budget=, proxy=)` warns on budget overruns unless `proxy=True`. `price_total` includes the commanders. Decks store `proxy`, and a proxy deck has `budget: None`.
- **`storage.py`**: saves decks to `decks/<slug>.json` plus a `.txt` export (Moxfield format). The `decks/` contents are gitignored.
  - Questions about a deck: `questions()` / `add_question()` / `delete_questions()` in `decks/.questions/<slug>.json`. `delete()` removes them and the game log too.
  - `set_extra(slug, key, value)` stores non-content data (guide, upgrade plan) in the current file without a new version.
  - **Versioning:** a new version is created when content changes (`_CONTENT_KEYS`) or a `change_note` is given. Re-validation alone does not create one.
  - Each version gets a full snapshot in `decks/.versions/<slug>/vNNNN.json` and a `history` entry: diff, from/to level, price, power.
  - `restore()` saves the old snapshot as a *new* version.
  - `copy()` creates a new slug with its own history.
  - Legacy decks without a `version` are snapshotted as v1 on their next save.
- **Proxy printing:** `proxy.py` and `imaging.py` (Pillow) integrate the MPC Autofill desktop tool (chilli-axe/mpc-autofill).
  - `plan(only_missing=, tokens=)`: `only_missing` skips cards the collection covers; `tokens=N` adds N copies of every token/marker (MPC Autofill `cardType` TOKEN, else Scryfall).
  - `plan()` picks one image per face. Priority: `proxies/<slug>/selection.json` › MPC Autofill search server (`/2/sources/`, `/3/editorSearch/`, `/2/cards/`, `/2/cardbacks/`) › Scryfall scan. Full images come from the CDN (`cdn.mpcautofill.com/images/google_drive/{small|full}/<id>.jpg`).
  - `prepare()` downloads into the shared cache `<MTG_DATA_DIR>/images`. Scryfall scans get bleed via `imaging.add_bleed`, 822×1122 px at 300 DPI.
  - `prepare()` hardlinks (or copies) every print file to `proxies/<slug>/images/<face name>.<ext>` plus `_Kartenrücken.*`, and clears stale files first.
  - It writes the order XML against those readable files (`sourceType` `Local File` only), plus `manifest.json`. Imageless cards get no slot.
  - It writes `prepared.json` with per-face `file` / `cache` / `original` / `origin` / `upscaled` / `dpi`. GUI routes `/print/prepared`, `/print/image?face=&kind=original|file|trim` (only paths listed there) and `/print/open-folder` drive the before/after view.
  - Opt-in AI upscaling: `prepare(upscale=)` defaults to setting `upscale` (off). It runs `realesrgan-ncnn-vulkan` (official zip v0.2.5.0 ships only `realesrgan-x4plus` and `-x4plus-anime`; `upscale_models()` lists what is installed; `-s 4`, `-m <exe dir>/models`, serialised by a `threading.Lock`) on Scryfall scans, then `add_bleed(dpi=600)`. If opted in and the tool or model is missing, it raises `ValueError`. An `UpscaleError` on a single card (e.g. no Vulkan GPU) falls back to 300 DPI with one aggregated warning. MPC scans are never upscaled.
  - Before upscaling, `imaging.descreen()` removes the print halftone of Scryfall scans. Setting `descreen`: off / light / normal (default) / strong.
  - `descreen()` is a slight Gaussian pre-blur (σ 0.45 / 0.6 / 0.8). It was chosen with real realesrgan-x4plus runs.
  - An FFT notch filter was tried and rejected: it caused streaks and invented texture.
  - Without it, the AI turns the ~3 px halftone into visible lines.
  - Cache names carry `imaging.descreen_tag()` (`-ds<DESCREEN_VERSION>-<strength>`). Bump `DESCREEN_VERSION` whenever the method changes, otherwise stale results get reused. `prepare(upscale=True)` deletes the retired notch-era `-ds<strength>` files.
  - `export_pdf()` builds home-printing sheets.
  - `autofill_command()` passes `--directory --browser --site --auto-save --no-image-post-processing`. Keep exactly one XML per folder, otherwise the tool prompts.
  - The tool shows InquirerPy arrow-key menus ("How would you like to upload this order?"). They need a real console, so plain pipes crash with `NoConsoleScreenBufferError`.
  - The GUI therefore runs it in a pseudo-terminal (`gui/terminal.py`: pywinpty/ConPTY on Windows, ptyprocess elsewhere; both are gui extras).
  - Raw output goes out as `term` SSE events and is rendered by the vendored xterm.js in `static/vendor/xterm`.
  - Keystrokes go through `/api/jobs/{id}/input` (`raw: true`). `/resize` also exists.
  - Fallback: `launch_autofill()` in its own console window (`CREATE_NEW_CONSOLE`), which the MCP tool uses too.
  - `settings.py`: user paths/servers in gitignored `mtgdeck.settings.json`, with env overrides.
  - To verify an XML against upstream, clone mpc-autofill and call `src.order.CardOrder.from_xmls_in_folder`.
- **`mcp_server.py`**: the MCP server, built with `mcp` **2.x**.
  - Use `from mcp.server.mcpserver import MCPServer`. `FastMCP` no longer exists in 2.x.
  - Tools are thin wrappers over the modules above.
  - `save_deck` re-validates before writing and stores the validation result inside the deck JSON.
- **`gui/app.py`**: a FastAPI app with vanilla JS in `gui/static/`.
  - **Frontend conventions** (`index.html`, `style.css`, `app.js`; no framework, no build step, vendored libs only):
    - Hash router: `#/new` · `#/job` · `#/deck/<slug>/<tab>` (tabs `karten|anleitung|testen|anpassen|fragen|partien|verlauf|drucken`) · `#/collection` · `#/orders[/<id>]` · `#/deskmat[/<id>]` · `#/glossary[/<term>]` · `#/opponents[/<id>]` · `#/tables[/<id>]` · `#/blacklist` · `#/settings`. Ctrl+K opens the quick search (`paletteItems()`, incl. glossary terms).
    - „Neues Deck“ has three modes (`buildForm.dataset.mode` build|find|import); import has sub-modes link|text|precon (`#import-box[data-imode]`).
    - The print studio (`#print-studio`) is one block: `mountPrintStudio()` moves it into the deck tab or the order page; print code uses `pctx()` (deck or `currentOrder`) instead of `currentDeck`.
    - After every rebuild (refine/retune job via `startJob({since})`, manual edit, upgrades, plan stage) `offerOrderAfterRebuild(slug, since)` asks via `orderDialog()` whether the new cards go into a collective order.
    - Function names are global in `app.js`: check for an existing name before adding one (`renderPlan` is the print plan, the upgrade plan is `renderUpgradePlan`).
    - Card list edits are collected in `edit` (add/qty/remove/cat) and saved in one request; test-hand odds are computed client-side (`renderOdds`, hypergeometric). Views are `<section class="view" data-view=…>`; tab switches use `history.replaceState`.
    - One job panel (`#job`) is moved into the slot of the view that started it (`startJob(id, title, {kind, slot, route, slug})`); the sidebar shows `#job-indicator` while it runs. `TOOL_LABELS` turns tool calls into plain-language status.
    - Use the design tokens in `style.css` (`--space-*`, `--fs-*`, `--radius*`, semantic colours incl. `--input-border` ≥ 3:1) for light and dark. Surfaces are `.panel`; `.card` is reserved for card rows (hover preview uses `.card[data-img]`).
    - Show and hide with the `hidden` attribute. Report with `toast()` and ask with `ask()` (a `<dialog>`), never `alert`/`prompt`/`confirm`. Keep one primary button per view and put rare options into `<details class="more">` or the ⋯ menu.
  - Each build or refine request becomes a `Job` that runs `claude_agent_sdk.query()`.
  - The job uses `cwd=`repo root and `setting_sources=["project"]` so it gets the skills and this file.
  - It starts the MCP server via `sys.executable -m mtgdeck.mcp_server`, with `permission_mode="dontAsk"`.
  - Events stream to the browser over SSE, with `Last-Event-ID` resume.
  - The finished deck is detected as the deck whose `updated` timestamp is ≥ the job start.
  - The prompts in `build_prompt` / `refine_prompt` / `finder_prompt` tell the agent it runs non-interactively. `_budget_line` holds the shared budget/proxy wording.
  - Retune jobs (`/api/retune`, `retune_prompt`) re-tune a saved deck to a new bracket/profile. `profile_lines()` renders a profile into prompt lines.
  - Generic background jobs via `_start_runner(runner)`. They emit `progress` / `print` / `console` events besides text.
  - Print routes: `/api/decks/{slug}/print/{plan,alternatives,choose,prepare,pdf,autofill,files/{xml|pdf}}`. Settings: `/api/settings`.
  - Version routes: `/api/decks/{slug}/versions[/{v}[/restore]]`, `/diff?a=&b=` and `/copy`.
  - Deck questions (`/api/decks/{slug}/ask`, `ask_prompt`, panel „Fragen zum Deck“) run `_run_claude(read_only=True, finish=)`: only `READ_ONLY_TOOLS` are allowed, `WRITE_TOOLS` plus Write/Edit/Bash are disallowed. The final answer (`ResultMessage.result`) goes to `storage.add_question()` (`decks/.questions/<slug>.json`), with images for its `[[Card]]` references (`_card_refs`). The last `ASK_HISTORY` Q&As go into the prompt for follow-ups. The skill side is `references/deck-questions.md`.
  - Upgrade jobs (`/api/decks/{slug}/upgrades`, `upgrade_prompt`, `UPGRADE_SCHEMA`) are read-only with structured output; `_enrich_upgrades()` drops invalid swaps and adds current prices/ownership. The GUI applies the chosen ones through `POST /api/decks/{slug}/cards`. `finish` callbacks receive `(job, ok, final_text, structured_output)`.
  - Guide (`/guide`) and upgrade-plan (`/upgrade-plan`) jobs are read-only with structured output and store their result in the deck via `storage.set_extra()`; they emit `guide` / `plan` SSE events.
  - Commander-finder jobs pass `output_format` (JSON schema `SUGGESTION_SCHEMA`) to the SDK. They read `ResultMessage.structured_output`, enrich it with card data (`_enrich_suggestions`) and emit a `suggestions` SSE event instead of a deck.

**Skill ↔ tools contract:**
- `SKILL.md` requires every build to end with `validate_deck` → fix → `save_deck`.
- It also requires `category` values from the fixed set in `references/deck-template.md`, because the GUI groups cards by them.
- If you change tool names or parameters, update `SKILL.md` and the GUI prompts too.

## Tests

- Tests must never hit the network.
- The mock also fakes a token search (`t:token` queries → two Treasure tokens). Route tests with background jobs use `with TestClient(app) as client:` so the event loop survives between requests.
- The mock also fakes the image generator (`image.pollinations.ai`, returns an image of the requested size; `fail` in the path → 500). Deskmat tests reuse `test_proxy._fake_esrgan` for the upscaler.
- The mock also fakes MTGJSON (`GraveTroupe_C99`), Archidekt deck 4242, Moxfield (v3 blocked, v2 answers; `blocked` ids fail), MTGGoldfish 777, TappedOut, Deckstats, the EDHREC average deck for Meren and German printings (`GERMAN`, `lang:de` searches).
- `tests/conftest.py` sets up an autouse fixture that:
  - replaces `http._client` with an `httpx.MockTransport` that fakes Scryfall, EDHREC and Spellbook;
  - redirects the cache, DB, decks, blacklist, table rules, collection and proxies to `tmp_path`;
  - disables throttling.
- Any card name starting with `Filler` is synthesized on demand. `deck_lines()` builds a legal 99-card main deck for Meren (BG).
- `CARDS` includes two opponent commanders (Atraxa, Praetors' Voice; Krenko, Mob Boss).
- `PRINTINGS` fakes specific printings for `/cards/collection` id and set+number lookups; "Pitiless Plunderer" carries `all_parts` (Treasure token, The Monarch) for token tests; `otag:`/`t:` searches return replacement candidates.
- Add new endpoints to the mock `handler`. It also fakes `cards.scryfall.io`, the MPC Autofill server (`MPC_SERVER`) and its CDN, returning generated images.
- `test_proxy.py` drives the terminal with a fake `autofill.py` that shows the same InquirerPy menu (inquirerpy is a dev dependency). The test answers it with raw arrow-key input.
- Async tests run with `asyncio_mode = "auto"`.

## Configuration

Environment variables (see README for the full table):
- `MTG_BULK_TYPE`, `MTG_BULK_MAX_AGE_DAYS`
- `MTG_DATA_DIR`, `MTG_CACHE_DIR`, `MTG_CACHE_TTL`
- `MTG_DECKS_DIR`, `MTG_BLACKLIST_FILE`, `MTG_TABLERULES_FILE`, `MTG_COLLECTION_FILE`, `MTG_PROXIES_DIR`, `MTG_DESKMAT_DIR`
- `MTG_AUTOFILL_PATH`, `MTG_MPCFILL_SERVER`, `MTG_CARDBACK`, `MTG_UPSCALER_PATH`
- `MTG_GUI_HOST`, `MTG_GUI_PORT`, `MTG_MAX_TURNS`
