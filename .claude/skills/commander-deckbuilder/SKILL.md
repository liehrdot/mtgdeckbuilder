---
name: commander-deckbuilder
description: Build or refine a Magic: The Gathering Commander (EDH) deck for a chosen commander and Commander Bracket (1-5), optionally with budget or proxy mode, card blacklist and theme, using the mtg MCP tools (Scryfall, EDHREC, Commander Spellbook). Use whenever the user wants a Commander/EDH deck built, a decklist generated, or an existing deck tuned, upgraded, made cheaper or moved to another bracket.
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
- **Blacklist**: call `get_blacklist` once. Those cards must never be in the deck
  (`validate_deck` reports them as errors; searches already hide them). If the user says a card
  should *never* be played again, offer to add it with `update_blacklist`.
- GUI/non-interactive mode (prompt says so): never ask, make reasonable assumptions and
  mention them in the deck description. Default bracket 3 if absent.

Read [references/brackets.md](references/brackets.md) for the bracket rules before choosing
cards. Call `bracket_rules` / `game_changers` if you need the live lists.

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
- **Never use blacklisted cards.**
- Prefer high synergy + high inclusion cards, but every card needs a reason to be in the deck.
- Have 2–4 clear win conditions and say what they are.
- Mana base: basics + on-color duals/utility lands fitting the budget; count colored pips
  (more sources for the dominant color). Colorless utility lands sparingly in 3+ color decks.

## 3. Validate and fix (mandatory)

1. Call `validate_deck` with commanders, the 99 (or 98) lines incl. basics (`"12 Forest"`), bracket,
   currency and `budget` / `proxy`.
2. Fix **every error** (card count, color identity, singleton, banned, blacklist, not found) and
   **every bracket violation**. Fix a budget overrun warning unless it is marginal. Check warnings (land count, low ramp/draw/removal) and the estimated
   bracket; if the estimate is above the target, cut the strongest pieces (Game Changers,
   fast combos, tutors).
3. Check `combos.included`: in bracket 1–2 there must be no 2-card infinite combos; in 3 only
   late-game ones. Mention intended combos to the user.
4. Repeat until legal and compliant.

## 4. Save and present

Call `save_deck` with a good deck name, all cards with a `category` (Ramp, Draw, Removal,
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

## Deck überarbeiten (refine)

For "make it cheaper / stronger / bracket X / more draw / swap Y":
1. `load_deck` the deck (or `import_deck` an Archidekt/Moxfield URL, or parse a pasted list).
2. `validate_deck` to see the current state.
3. Make targeted swaps (keep the count at 100, keep what works), research replacements as above.
4. `save_deck` with the **same slug**, then list the changes as `+ in` / `- out` with reasons.

## Analyse only

If the user only wants an assessment ("which bracket is my deck?"), run `validate_deck` and
explain the bracket result, Game Changers, combos, curve and weaknesses — without saving,
unless asked.
