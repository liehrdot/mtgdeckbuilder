# Power tuning inside and across brackets

A bracket is a wide band. Players talk about **lower / middle / upper** of a bracket
("lower 4", "upper 3"). The `power_profile` captures that plus house rules and a style:

```json
{"tier": "low|mid|high", "max_game_changers": 2, "max_tutors": 3,
 "allow_two_card_combos": false, "allow_extra_turns": false, "allow_mass_land_denial": false,
 "style": "witzig", "notes": "free text"}
```

- House rules may only be **stricter** than the bracket (bracket 4 without Game Changers ✔,
  bracket 3 with 5 Game Changers ✘ – that is bracket 4).
- `validate_deck` returns `bracket.power` = heuristic score on the bracket scale
  (1.0–5.99; 3.8 = upper bracket 3) with its `components`, and warns when the score is more than
  ~0.4 away from the target tier. It is a guide: always also judge speed, consistency and how
  the deck wins, and explain the level in the description.

## Scale

| Score | Meaning |
|---|---|
| x.0–x.33 | lower bracket x – weaker end, casual-leaning |
| x.33–x.66 | middle bracket x |
| x.66–x.99 | upper bracket x – as strong as the bracket's rules allow |

One step up from "upper 3" is "lower 4"; one step down from "lower 4" is "upper 3".

## Levers (strongest first)

**Stronger (step up)**
1. Game Changers (within the limit / house rule) – efficient tutors, fast mana, card advantage engines.
2. Compact win conditions: 2-card combos (only bracket 4+, or late-game in 3), fewer, stronger finishers.
3. Fast mana: Sol Ring-style rocks, 2-mana rocks that make 2, Ancient Tomb-type lands; lower the curve.
4. Tutors for the key pieces (Game Changer tutors count against the GC limit).
5. Cheap and free interaction: 1–2 mana removal, instant speed, free counterspells (4+).
6. Better mana base: untapped duals, fetches/shocks (budget/proxy permitting).
7. Cut the cute: replace win-more and slow value cards with redundancy of the best effects.

**Weaker (step down)**
1. Remove Game Changers / reduce to the house rule; replace with fair equivalents
   (Demonic Tutor → Diabolic Tutor/Sidisi-style value, Rhystic Study → Phyrexian Arena-style draw).
2. Remove 2-card combos; win through board, value engines or big splashy spells.
3. Replace fast mana with normal ramp (Signets/Talismans → Mind Stone, land ramp at 3 mana).
4. Fewer tutors; more redundancy and card draw instead.
5. Slower, fair interaction (sorcery-speed removal, fewer counterspells); keep enough to be fun.
6. Raise the curve slightly with splashy, memorable cards that fit the theme.

Change as few cards as needed for one step (typically 5–15 swaps per sub-tier). Keep the deck's
identity: the commander's plan and favourite cards should survive.

## Style / vibe (`style`)

The style shapes *which* cards fill the slots at the same power level:
- **witzig / lustig**: chaos and random effects (coin flips, Possibility Storm-like, Wild Magic),
  memorable one-of-a-kind cards, "did that just happen" moments, silly flavour, political deals.
  Avoid oppressive locks and long solitaire turns.
- **chaotisch**: random effects, swapping control, symmetrical wheels, everybody-benefits chaos.
- **politisch**: voting, monarch, goad, deals, "each opponent chooses", group-hug-with-a-twist.
- **thematisch / flavor**: cards matching the creature type, story, set or art; mechanics that tell a story.
- **gemein / stax-light**: taxing effects – note that heavy stax pushes up the perceived bracket.
- **Casual / Kitchen Table**: fair, interactive, no early kills, room for pet cards.

## Workflow for "make it lower 4 / upper 3 / bracket 2 but funny"

1. `load_deck`, then `validate_deck` with the **new** bracket and `power_profile` to see the
   current score, violations and warnings.
2. Decide the direction (score vs. target) and pick levers from above; respect budget/proxy,
   blacklist and the style.
3. Swap cards (keep 100), validate again until compliant and the score is near the target tier.
4. `save_deck` with the same `slug`, new `bracket`, `power_profile` and a `change_note`, e.g.
   "Oberes 3 → unteres 4: +Demonic Tutor, +Mana Vault, -Diabolic Tutor, -Mind Stone; Combo X ergänzt".
   Update `description`/`notes` to the new level.
5. Tell the user: old → new level, score before/after, the swaps (+/-) with one-line reasons.
