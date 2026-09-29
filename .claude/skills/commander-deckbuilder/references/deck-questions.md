# Answering questions about a saved deck

Read-only: never call `save_deck`, `restore_deck_version`, `copy_deck` or `update_blacklist` while
answering. Suggest changes as `+ in` / `- out` with a reason; the user applies them via refine.

## Always first

1. `load_deck` → commanders, cards with categories, description, last validation (bracket, power
   score, Game Changers, combos, warnings).
2. Pull what the question needs, not everything:
   - exact card behaviour → `get_cards` (Oracle text; never quote rules text from memory)
   - combos / win lines → `find_combos` with the deck's commanders and cards
   - bracket or power doubts → `validate_deck` (live) and `bracket_rules`
   - opponents → see "Matchups" below

Answer the actual question first (one or two sentences), then the reasoning. Name concrete cards
as `[[Card Name]]`. Keep it short enough to read at the table.

## Typical questions

**Strategy / "Wie spielt man das Deck?"**
- Game plan in one sentence (e.g. "Aristocrats: sacrifice outlets + recursion, drain with X").
- Early / mid / late game: what to do on turns 1–3, how the engine assembles, how it closes.
- Win conditions (list the real ones incl. combos from `find_combos`) and backup plans.
- Key cards (engines, payoffs) vs. replaceable filler.
- Mulligan: what a keepable hand needs (lands, ramp, a piece of the engine).

**Strength / "Ist das Deck zu stark (für meine Runde)?"**
- Use the validation: bracket, sub-tier and power score (1.0–5.99), Game Changers, tutors, fast
  mana, 2-card combos and how early they can happen, mass land denial, extra turns.
- Compare with the bracket definition (references/brackets.md, references/power-tuning.md) and
  say which cards push it up. If it is too strong or weak, give 3–6 concrete swaps.

**Matchups / "Wie schlägt es sich gegen XY?"**
- XY is a commander → `edhrec_average_deck` (with the deck's bracket if sensible) and
  `edhrec_recommendations` for its typical cards and themes. XY is a saved deck → `load_deck`.
  XY is a deck link (Archidekt, Moxfield, MTGGoldfish, TappedOut, Deckstats, EDHREC) → `import_deck`. XY is an archetype ("Stax", "Voltron",
  "cEDH-Turbo") → reason from the archetype, say so.
- Compare speed (curve, ramp, combo turn), interaction (removal, wipes, counters, graveyard hate,
  artifact/enchantment removal) and resilience (recursion, protection) on both sides.
- Name the threats you must answer, which of your cards answer them, the dangerous cards of
  theirs for your plan, and a verdict (favoured / even / unfavoured, with why).
- Optional: 2–4 sideboard-style swaps that improve the matchup without breaking the bracket.

**Weaknesses / "Was fehlt?"**
- Role counts vs. the minimums (ramp, draw, removal, wipes), curve, colour sources, single points
  of failure (commander-dependent?), vulnerability to graveyard hate, wipes, stax.

**Card choices / "Warum ist X drin?" / "Ist Y besser als X?"**
- Explain the role of X in this deck. For Y: Oracle text of both, synergy with the commander,
  mana value, price (unless proxy deck), bracket impact (Game Changer?), blacklist
  (`get_blacklist`).

**Rules / interactions**
- Quote the relevant Oracle text via `get_cards`, then explain step by step. If a ruling is
  uncertain, say so instead of guessing.

## Follow-ups

The prompt may include earlier questions and answers about the same deck. Use them to resolve
references ("und gegen Kinnan?", "welche davon?") but re-check facts with the tools; the deck may
have changed since (version number).
