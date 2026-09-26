---
name: commander-finder
description: Suggest Magic: The Gathering Commanders (EDH) that match a free-form wish - favorite colors, creature types, mechanics, play style, flavor, bracket, budget. Use when the user has no commander yet, asks "which commander should I play", "suggest a commander for ...", or wants ideas before building a deck.
argument-hint: "<Wunsch, z. B. 'Drachen, viel Ramp, Bracket 3'>"
---

# Commander Finder

Turn a vague wish into 3-5 concrete, well-reasoned commander suggestions that can then be built
with the `commander-deckbuilder` skill. Use the `mcp__mtg__*` tools; never suggest a card
without checking that it exists and is a legal commander. Reply in the user's language (German default).

## 1. Understand the wish

Extract (don't ask unless the wish is empty):
- colors / color identity (or "no preference", "no blue", "mono-color"),
- mechanics & themes (tokens, +1/+1 counters, sacrifice, spellslinger, landfall, voltron, graveyard ...),
- creature types / flavor (dragons, vampires, a character, a plane, art style),
- play style (combo, control, stompy, group hug, politics, "no stax"),
- bracket (default 3), budget or **proxy** (proxy = price irrelevant),
- things to avoid (cards on the blacklist via `get_blacklist` are never suggested).

## 2. Collect candidates (cast a wide net: ~10-20)

- `find_commanders` with Scryfall syntax, several searches from different angles, e.g.
  `id=wg o:token`, `t:dragon`, `id<=br o:sacrifice`, `o:"whenever you cast" id=ur`,
  `id=bg o:"+1/+1 counter"`. `is:commander` is added automatically. Also consider partners
  (`o:partner`) and backgrounds (`t:background`) when a pair fits better.
- `local_card_search` with `commanders_only: true` and Tagger `tags` (e.g. `["sacrifice-outlet"]`,
  `["token-generator"]`, `["ramp"]`) plus `color_identity` for fast offline results.
- Results come sorted by EDHREC popularity; include at least one less-played "hidden gem"
  when it genuinely fits.

## 3. Shortlist and check (3-5 finalists)

For each finalist:
- `get_cards` to read the exact oracle text – does the commander actually *do* what the user wants?
- `edhrec_recommendations` (with `bracket`) to see deck count (`num_decks`), themes and whether
  support cards exist in the requested bracket/budget. Few decks = niche but fine; no page = very new or rare.
- Bracket fit: can the commander be built fairly in bracket 1-2 (not an inherently fast combo/stax
  engine)? Is it strong enough for bracket 4? Is the commander itself a Game Changer (`game_changer`)?
- Budget: are the key support cards affordable (skip if proxy)?

Prefer variety: different colors or angles, so the user has a real choice.

## 4. Present

For each suggestion: name (+ partner), colors, archetype, 2-3 sentences *why it fits the wish*,
how it plays in the requested bracket, and a one-line strategy the deckbuilder can use.
End with: "Soll ich ein Deck mit einem davon bauen?" – if yes, continue with the
`commander-deckbuilder` skill using that commander, bracket, budget/proxy and strategy.

In GUI mode (the prompt says so) do not ask anything and do not build or save a deck; return
the suggestions in the requested structured format with exact English card names.
