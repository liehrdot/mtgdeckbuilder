---
name: commander-deckbuilder
description: Build, refine or re-tune a Magic: The Gathering Commander (EDH) deck for a chosen commander and Commander Bracket (1-5) incl. sub-tiers (lower/upper bracket), house rules (e.g. max. Game Changers) and style, optionally with budget or proxy mode, card blacklist and theme, using the mtg MCP tools (Scryfall, EDHREC, Commander Spellbook). Use whenever the user wants a Commander/EDH deck built (also "the strongest deck against my playgroup / the decks I play against"), a decklist generated, or an existing deck tuned, upgraded, downgraded, made cheaper or moved up/down a bracket or sub-tier. Also use it to answer questions about a saved deck (strategy, how to play it, power level, matchups against other commanders, weaknesses, card choices).
argument-hint: "<Commander> [bracket 1-5] [budget] [theme]"
---

# Commander Deckbuilder

You build a legal, playable 100-card Commander deck that fits the requested **bracket**.
All data comes from the `mtg` MCP server (`mcp__mtg__*`). Never guess card text, legality, prices
or color identity from memory — look it up.

Reply in the user's language (German by default in this project).

## 0. Clarify the request

Gather: commander (+ partner/background), **bracket (1-5)**, budget (total, EUR by default)
**or proxy**, theme/strategy, special wishes (pet cards, cards to avoid, "no infinite combos", ...).

- Interactive session: if the bracket is missing, ask once, briefly. If the user has **no
  commander** yet (only colors/theme/vibe), switch to the `commander-finder` skill first and
  come back here with the chosen commander.
- **Proxy**: if the user will proxy the deck ("ich proxe", "Proxies", "Preis egal"), prices do
  not matter at all. Ignore any budget, pick the best cards for bracket and strategy, and pass
  `proxy: true` to `validate_deck` and `save_deck`. Still respect the bracket.
- **Blacklist**: call `get_blacklist` once. Its cards must never be in the deck, and neither
  may any card that breaks one of its `rules` (e.g. True Duals, cheap tutors, fast mana, a price
  limit, 2-card combos). `validate_deck` reports both as errors; searches already hide them.
  Rules with `checked: false` are free-text wishes – respect them yourself. If the user says a card
  or a kind of card should *never* be played again, offer to add it with `update_blacklist`
  (terms like "Günstige Tutoren" or "teurer als 20 €" work directly; `@<text>` stores free text).
- **Table rule (Tischregel)**: a named rule set of a playgroup (`table_rules`): max. bracket, Game
  Changers, tutors, deck budget (counts for proxy decks too), no proxies, forbidden card groups and
  cards, free-text agreements. A deck follows at most one (`load_deck` shows `table_rule_info`). If the
  prompt or the user names one ("für die Freitagsrunde"), pass its id as `table_rule` to `validate_deck`
  and `save_deck`; violations are errors. When refining, `save_deck` keeps the deck's table rule unless
  you pass `table_rule` ("" removes it). The stricter of table rule, bracket and `power_profile` wins.
  Create or change rule sets with `update_table_rule` only when the user asks.
- GUI/non-interactive mode (prompt says so): never ask, make reasonable assumptions and
  mention them in the deck description. Default bracket 3 if absent.

Read [references/brackets.md](references/brackets.md) for the bracket rules before choosing
cards. If the user wants a **sub-tier** ("unteres/oberes Bracket 3", "lower 4"), **house rules**
("Bracket 4 ohne Game Changer", "Bracket 3 mit max. 2 Game Changern", "keine Combos") or a
**style** ("Bracket 2, aber witzig"), put it into a `power_profile` and read
[references/power-tuning.md](references/power-tuning.md). Call `bracket_rules` / `game_changers` if you need the live lists.

## 1. Research

1. `get_cards` on the commander(s): exact name, color identity, oracle text. Identify what the
   commander *wants* (triggers, resources, weaknesses).
2. `card_db_status`. If the local DB is missing, continue with the live API; you may suggest
   `update_card_database` to the user (takes a few minutes, not needed for one deck).
3. `edhrec_recommendations` for the commander **with `bracket`** (and `budget: "budget"` for
   tight budgets). If that page does not exist, retry without bracket, then without budget.
   Look at `themes`; if the user named a theme, call it again with `theme`.
4. Optionally `edhrec_average_deck` (same filters) as a skeleton.
5. Fill gaps with targeted searches:
   - `local_card_search` (fast, offline): `color_identity`, `tags` (Tagger: `ramp`, `draw`,
     `removal`, `sweeper`, `tutor`, `counterspell`, `extra-turn`, `mana-rock`, `mana-dork`, ...),
     `max_price`, `exclude_game_changers`.
   - `search_cards` (live Scryfall syntax), e.g. `id<=bg otag:removal -is:gamechanger eur<3`.

## 2. Build the list

Target composition (adjust to the commander and curve — see
[references/deck-template.md](references/deck-template.md)):

| Role | Count |
|---|---|
| Lands | 35–38 (fewer only with very low curve / lots of cheap ramp) |
| Ramp | 10–12 |
| Card draw / advantage | 10 |
| Targeted removal / interaction | 8–10 |
| Board wipes | 2–4 (fewer in creature decks) |
| Synergy / theme + win conditions | rest (~25–30) |

Rules while picking:
- **Exactly 100 cards** including commander(s). Singleton (except basic lands and cards that say
  "A deck can have any number of cards named ..."). Only cards within the commander's color identity.
- **Respect the bracket** (Game Changer limit, no mass land denial in 1–3, extra turns, 2-card
  combos). Prefer the EDHREC data filtered to the bracket.
- **Respect the budget** (unless proxy): track prices (`price_eur`/`price_usd`, cheapest printing
  in the local DB). Leave ~10 % headroom. Basics count as ~0. Pass `budget` to `validate_deck` /
  `save_deck` so overruns are flagged. For proxy decks don't use the EDHREC `budget` filter.
- **Never use blacklisted cards** or cards that break a blacklist rule.
- Prefer high synergy + high inclusion cards, but every card needs a reason to be in the deck.
- Have 2–4 clear win conditions and say what they are.
- Mana base: basics + on-color duals/utility lands fitting the budget; count colored pips
  (more sources for the dominant color). Colorless utility lands sparingly in 3+ color decks.

## 3. Validate and fix (mandatory)

1. Call `validate_deck` with commanders, the 99 (or 98) lines incl. basics (`"12 Forest"`), bracket,
   currency, `budget` / `proxy`, `power_profile` and `table_rule` (if any).
2. Fix **every error** (card count, color identity, singleton, banned, blacklist, table rule, not found) and
   **every bracket violation** (incl. the house rules of the power profile). If the power score
   (`bracket.power`) is far from the target tier, tune with the levers in power-tuning.md.
   Fix a budget overrun warning unless it is marginal. Check warnings (land count, low
   ramp/draw/removal) and the estimated bracket; if the estimate is above the target, cut the
   strongest pieces (Game Changers, fast combos, tutors).
3. Check `combos.included`: in bracket 1–2 there must be no 2-card infinite combos; in 3 only
   late-game ones. Mention intended combos to the user.
4. Repeat until legal and compliant.

## 4. Save and present

Call `save_deck` (with `power_profile` if one was given) with a good deck name, all cards with a `category` (Ramp, Draw, Removal,
Board Wipe, Synergy, Win Condition, Protection, Utility, Land), a short `description` (game plan,
key synergies, assumptions) and `notes` (mulligan/play tips, combos, upgrade options for later).
If `save_deck` reports errors, fix and save again with the same `slug`.

Then present to the user:
- Deck name, commander, bracket (target vs. estimated), total price (for proxy decks: "Proxy",
  real value only as information).
- Game plan in 3–5 sentences, win conditions.
- Decklist grouped by category (compact).
- Notable choices / what was left out for bracket or budget reasons, upgrade ideas.
- Where it is saved (`decks/<slug>.txt` imports into Moxfield/Archidekt) and that it can be
  viewed in the GUI (`uv run mtg-gui`).

## Gegen die Runde bauen (meta build)

For "bau das stärkste Deck gegen meine Runde / gegen die Decks, gegen die ich spiele":
1. Read the field: `opponent_decks` (commanders, traits, the user's observations, record – no lists; look up
   typical cards of each commander with `edhrec_recommendations`), `list_decks` + `load_deck` / `deck_games`
   for the user's own decks (what exists, how they fared, who beat them). Name the main threats: how each
   opponent wins, how fast, what hurt the user.
2. Commander: if none is given, pick one (commander-finder approach, `find_commanders`) that is well placed
   against exactly this field – speed, the right interaction, resilience against their answers – and that the
   user does not play yet. Briefly say why it beats the alternatives.
3. "Strongest" = best chances against these opponents **inside** the bracket, sub-tier, house rules, table
   rule, budget and blacklist – never above them.
4. Build a coherent deck with its own win plan plus targeted interaction (answers that also work outside this
   pod; no dead hate cards for a single opponent). Then validate, fix and save as usual.
5. `description`: why the deck is strong against this pod. `notes`: a short game plan per opponent (what to
   watch for, which answers to hold back).

When asked only for **suggestions** ("schlag mir 3–5 Decks gegen meine Runde vor"), do steps 1–3 for several
clearly different commanders (colours, strategy, speed), ranked by expected strength, and present each with:
why it beats this pod, win plan, one line per opponent, 5–8 key cards (checked with the tools), main weakness
and fit with bracket/table rule/budget. Build nothing until the user picks one. If web research is allowed,
use it for current meta information on the opponents' commanders and your candidates, and name the sources.

## Bracket rauf/runter & Feinabstimmung (retune)

For "make it upper 3 / lower 4 / bracket 4 without Game Changers / bracket 2 but funny" on an
existing deck: follow the workflow in [references/power-tuning.md](references/power-tuning.md)
and always save with the same `slug` and a `change_note` (it goes into the deck's history).
Report old → new level, power score before/after and the swaps with reasons.

## Versionen (Verlauf, Diff, Rückgängig, Kopie)

Every `save_deck` creates a version with a snapshot. Use:
- `list_deck_versions` – history with notes, cards in/out, level, price, power per version.
- `compare_deck_versions` – "what changed since version 2?", "compare before/after the retune".
- `restore_deck_version` – "undo", "go back to the bracket 3 version" (saved as a new version).
- `copy_deck` – "make a copy as bracket 2 variant", optionally from an old `version`; then
  retune the copy so both variants exist side by side.
- `export_deck` with `version` – decklist of an old version.

## Proxies drucken (MPC Autofill)

When the user wants to print/proxy a deck (always offer it for proxy decks after saving):
1. `create_proxy_order` (slug; optional `stock`, `foil`, `version`) – downloads and prepares all
   images (MPC Autofill community scans if a server is set in `proxy_settings`, else Scryfall
   scans with generated bleed), double-faced backs and a cardback into `proxies/<slug>/`.
   AI upscaling of Scryfall scans (`upscale: true`, Real-ESRGAN → 600 DPI) is opt-in: only pass it
   when the user asks for it or has enabled it in the settings.
2. Home printing: `export_proxy_pdf` (A4/Letter, 3 x 3 per page with cut marks).
3. MakePlayingCards: `launch_proxy_tool` starts the MPC Autofill desktop tool (browser login and
   upload happen on the user's machine) – only when asked. If the tool is missing, explain:
   put `autofill-windows.exe` into `tools/` or set the path via `proxy_settings`.
Mention the result: number of cards, MPC bracket, images from MPC Autofill vs. Scryfall, missing
cards. Individual images can be swapped in the GUI's Druckstudio.

## Deck überarbeiten (refine)

For "make it cheaper / stronger / bracket X / more draw / swap Y":
1. `load_deck` the deck (or `import_deck` a deck link – Archidekt, Moxfield, MTGGoldfish, TappedOut,
   Deckstats, EDHREC average deck –, `import_precon` a starter deck found with `search_precons`, or parse a
   pasted list). `deck_games` shows logged games and their most common problems – use them to decide what to change.
   `opponent_decks` lists the decks the user plays against (commander, traits, observations, record – no card
   lists; typical cards via `edhrec_recommendations` for that commander). Prepare for the relevant ones with
   fitting interaction and answers, without turning the deck into a pure hate deck. When the user tells you
   something about an opponent ("Tims Atraxa gewinnt mit Thassa's Oracle"), offer to note it with `update_opponent_deck`.
2. `validate_deck` to see the current state.
3. Make targeted swaps (keep the count at 100, keep what works), research replacements as above;
   `similar_cards` lists replacements for one card (same role, fits the colours, not in the deck).
4. For a handful of swaps use `edit_deck` (slug, `add`, `remove`, `change_note`) – it re-validates
   and saves a new version without resending the list. For bigger rebuilds use `save_deck` with the
   **same slug** and a short `change_note`. Then list the changes as `+ in` / `- out` with reasons.

## Sammelbestellungen (collective print orders)

To print cards from several decks, single cards and tokens together (e.g. "pack die Upgrades und 50 Treasure in
meine Bestellung"): `print_orders` lists the orders, `update_print_order` adds cards (`add_cards`), tokens
(`add_tokens`, any amount) or a deck (`from_deck`, optionally `only_missing`) – a new order is created when the
name does not exist. Printing itself (images, MPC Autofill, PDF) happens in the GUI page „Sammelbestellungen“.

## Meine Sammlung (collection)

The user can keep a collection (cards they own, real or proxy, with quantity and printing).
- "Build from my collection" / „Sammlung bevorzugen“: `collection_search` with the commander's colour
  identity lists owned cards (`real`, `proxy` counts). Prefer them where they fit as well as the
  alternatives; owned cards cost nothing, so for a budget only missing cards count.
- `collection_status(slug)` compares a saved deck with the collection: owned real/proxy, missing
  cards with prices (shopping list) and cards shared by several decks without enough copies.
- `update_collection` adds cards – only when the user asks for it.

## Analyse only

If the user only wants an assessment ("which bracket is my deck?"), run `validate_deck` and
explain the bracket result, Game Changers, combos, curve and weaknesses — without saving,
unless asked.

## Fragen zum Deck (Q&A)

For questions about a saved deck — strategy, how to pilot or mulligan it, win conditions, whether
it is too strong for a table, matchups against a commander/deck/archetype, weaknesses, why a card
is in, rules interactions — follow [references/deck-questions.md](references/deck-questions.md).
Read-only: never save; suggest swaps as `+ in` / `- out` instead.

## Deck-Anleitung (Spickzettel)

For a play guide of a saved deck (the GUI tab „Anleitung“ asks for it with a JSON schema): load the
deck, read the key cards with `get_cards` and check combos with `find_combos`. Write for a
beginner who pilots the deck for the first time and keep it to one printed page:

- **plan**: the game plan in at most three sentences.
- **early / mid / late**: 2–4 concrete bullets each (what to cast or hold, when the commander comes down).
- **mulligan**: keep/mulligan rules (lands, ramp, a piece of the engine; Commander's first mulligan is free).
- **key_cards**: 4–8 cards *from the deck* with their role and when to play them.
- **win_conditions**, **watch_out** (weaknesses, threats, easy mistakes), **tips** (interactions that are easy to miss).

Card names as `[[English Name]]`, German text, read-only (never save).
