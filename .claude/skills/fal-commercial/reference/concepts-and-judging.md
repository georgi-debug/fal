# Concepts and AI judging

No fal spend in this phase. On the reference run it took 14 agents, ~54 minutes and
$31.38 of Claude, and the judging improved the result: the winner absorbed 18 grafted ideas.

## Four concept agents, one angle each

All get the same context (the measured analysis, style bible, brand notes, budget) and a
different angle:

| Angle | Brief to the writer |
|---|---|
| Faithful | A beat-for-beat, second-for-second remake of the reference's structure in the new world |
| Device | A world-native trigger for the theme (e.g. a dandelion wish), carried through the spot |
| Brand-native | Transformations that quietly show what the product enables, with no UI or jargon |
| Showstopper | The most memorable images the video model can really make, inside the reference's structure |

Hard requirements for every writer:
- the reference's pacing: similar beat count and escalation, hidden transitions
- the style bible followed exactly
- the brand's type system (title, split captions, tagline)
- one fictional adult protagonist
- a stated generation budget (the reference used ~$300 of video)
- avoid what AI video fails at: readable in-world text, complex hand interactions,
  close-up crowds of similar faces, single takes over ~15 s, thin props carried across
  many clips

Each returns a treatment and a full timed beat sheet (fields in `beat-sheet.md`).

## Three judges, three lenses

| Judge | Lens |
|---|---|
| J1 | A top commercial creative director: idea strength, memorability, how well it sells the theme |
| J2 | An AI-video VFX producer who has shipped spots on these models: identity consistency, coherent motion, stitchable transitions, cost within budget |
| J3 | The editor who cut the reference: pacing fidelity, escalation, transition craft, rhythm |

Each scores every concept 1–10 on five criteria (total /50):
`pacing_fidelity`, `look_fidelity`, `brand_message`, `cleverness`, `producibility`,
and returns `best_ideas_to_graft`, `weaknesses` and a winner.

Give judges the measurable assets too (e.g. the end-card file): two judges caught a
hand-off error by measuring it.

## Director synthesis

One agent takes the judges' winner (majority vote, then combined score), grafts the best
ideas from the others, fixes every named weakness, and writes `plan/final.json`:
treatment, timed beats, copy, music brief, sound design, transition plan, generation plan
and `grafted_ideas`. Then a short note to the user: winner, scores, grafts, risks.

## What the reference run learned

- **Pacing carried it.** The winner was the only concept on the reference's clock (a
  120 BPM grid within ~0.1 s); both votes it won cited that first.
- **Producibility decides close calls.** The cleverest concept needed one thin prop
  (a dandelion) across ~14 clips; the VFX judge called it "a production trap".
- **Decorative layers die in review.** Surviving grafts were structural (timing,
  hand-off, silent clouds) or single clear images (a pond floor, a lamb leaving a painting).
  Floating particles, doodles and critters were all cut by the human reviewer.
- **One lens per judge works:** most of the 18 grafts came from the judges' lists.
