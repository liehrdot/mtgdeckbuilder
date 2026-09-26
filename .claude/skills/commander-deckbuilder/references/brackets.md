# Commander Brackets (Wizards of the Coast, beta)

State: rework of **21 Oct 2025** (brackets defined by expected earliest game end, tutor limits
removed) and Game Changer update of **9 Feb 2026** (Biorhythm unbanned → Game Changer, Farewell
added). Source: magic.wizards.com announcements. The Game Changer list changes — always use the
`game_changers` tool (live from Scryfall `is:gamechanger`) instead of memory.

| | 1 Exhibition | 2 Core | 3 Upgraded | 4 Optimized | 5 cEDH |
|---|---|---|---|---|---|
| Expected earliest game end | turn 9+ | turn 8+ | turn 6+ | turn 4+ | any |
| Game Changers | 0 | 0 | max. 3 | unlimited | unlimited |
| Mass land denial | no | no | no | yes | yes |
| Extra turns | no | few, not chained | few, not chained | yes | yes |
| 2-card infinite combos | no | no | not early game | yes | yes |
| Tutors | no limit (efficient ones are Game Changers) | | | | |

## What the brackets mean when choosing cards

- **1 Exhibition** – theme and flavor first (e.g. "only Squirrels", "only cards from one plane").
  Power is intentionally low; weak-but-thematic cards are fine. Games are long.
- **2 Core** – precon level. Solid but unoptimized. Straightforward, fair game plan; fair
  removal and a few strong staples are fine, but no Game Changers and no 2-card infinites.
- **3 Upgraded** – well-tuned synergy deck. Up to 3 Game Changers, good mana, efficient
  interaction. Combos only as late-game finishers (not a turn-5 kill).
- **4 Optimized** – as strong as the deck can be without cEDH meta focus: fast mana, tutors,
  compact combos, free interaction, Game Changers without limit.
- **5 cEDH** – built for the competitive meta: fastest wins, max. consistency, stax/free spells.

The list-based checks are a minimum. Also judge speed and consistency: a deck with 0 Game
Changers but lots of fast mana and tutors can still play like bracket 4.

## Mass land denial (examples)

Cards that destroy, exile or bounce many lands, keep them tapped, or change what they produce
without replacement: Armageddon, Ravages of War, Jokulhaups, Obliterate, Ruination, Sunder,
Winter Orb, Static Orb, Stasis, Rising Waters, Back to Basics, Blood Moon, Magus of the Moon,
Hokori Dust Drinker ... Commander Spellbook's estimate (used by `validate_deck`) flags them too.

## Commander Spellbook bracket tags

`validate_deck` also asks Commander Spellbook for a bracket estimate. Combo tags:
E Exhibition, C Core, O Oddball, P Powerful, S Spicy, R Ruthless, B Banned. The mapping to
bracket numbers in the tool output is approximate — treat it as a second opinion.
