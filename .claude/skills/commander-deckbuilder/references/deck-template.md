# Deck template & heuristics

## Composition (99 cards + commander)

| Role | Count | Notes |
|---|---|---|
| Lands | 36 (±2) | 38 for landfall/high curve, 33–34 with avg. MV < 2.5 and 12+ ramp |
| Ramp | 10–12 | 2-MV rocks/dorks/land ramp first; fits the colors (green: land ramp) |
| Card draw | 10 | repeatable engines > one-shot draw |
| Removal | 8–10 | flexible (any permanent) > creature-only; instant speed preferred |
| Board wipes | 2–4 | 0–2 in go-wide creature decks, more in control/voltron |
| Protection | 2–4 | for commander-dependent decks (voltron, engine commanders) |
| Theme/synergy | 20–30 | cards that work with the commander |
| Win conditions | 2–4 | how does the deck actually end the game? |

Many cards fill two roles (e.g. a creature that draws). Counting roles is a guide, not a law.

## Mana curve targets (non-land cards)

- Average MV around 2.8–3.3 for bracket 2–3, lower (≤ 2.5) for bracket 4–5.
- Plenty of 1–3 drops, few 6+ drops (unless the deck ramps into them on purpose).

## Mana base

- Colored sources: ~ (pips of that color / all pips) × colored lands, minimum ~10 sources for a
  main color in 2-color decks.
- Budget order: basics → guildgates/tapped duals → pain/check/slow lands → shocks/fetches → original duals.
- Utility lands only when they serve the plan (e.g. Reliquary Tower for draw decks, Rogue's Passage for voltron).
- Command Tower, Arcane Signet, Sol Ring: staples in basically every multicolor deck
  (Sol Ring and Command Tower also fine in bracket 1–2).

## Budget

- Budget = total price of all 100 cards (cheapest printing). Basics ≈ 0.
- Replace expensive staples with functional reprints/alternatives (e.g. Talismans/Signets for Mana
  Crypt-like rocks, Rampant Growth/Nature's Lore for fetch ramp).
- EDHREC `budget: "budget"` pages are a good source.

## Categories for save_deck

Use exactly these where possible so the GUI groups nicely:
`Ramp`, `Draw`, `Removal`, `Board Wipe`, `Protection`, `Synergy`, `Win Condition`, `Utility`, `Land`.
