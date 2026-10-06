# Budget and schedule

Estimates from the unit costs in `../fal-media/reference/models-and-settings.md`.
Re-price with `get_pricing` at project start; present every figure to the user as an
estimate at list price.

## What the reference run cost (60 s hero only)

| Item | Output | fal |
|---|---|---|
| Seedance 1080p finishes | 26 clips | $133.07 |
| Seedance 480p drafts | 129 drafts | $125.40 |
| Nano Banana Pro | 363 images | $54.45 |
| One US-endpoint 1080p test | 1 clip | $8.19 |
| AI reviews (audio + video) | 52 | $6.44 |
| Music (Lyria + ElevenLabs) | 30 takes | $6.00 |
| SFX | 36 | $0.37 |
| **fal total** | | **$333.92** |
| Claude: shot agents / main session / analysis + concepts | 2,316 calls | $140.70 / $32.65 / $31.38 |
| **Total** | ~3.6 h active | **$538.65** |

Where it leaked: $35.48 of finished clips not used; ~5.9 drafts per used clip; $11.23 on
an abandoned regeneration; Claude agents re-reading context.

## Planning model for hero + cutdowns

| Deliverable | What it needs | Est. fal | Est. Claude |
|---|---|---|---|
| 60 s hero | ~22 clips × ~3.5 drafts (~5 s, $0.21/s) ≈ $80; 22 finishes (~$5 each) ≈ $113; keyframes ≈ $45; music, SFX, reviews ≈ $13 | ~$250 | ~$120–150 |
| 30 s cutdown | A new cut list over hero clips + a 30 s music take | ~$2 | ~$10 |
| 15 s cutdown | A new cut list + a 15 s music take | ~$1 | ~$10 |
| 9:16 vertical | ~8 wide shots re-keyframed, drafted and finished natively; the rest centre-cropped | ~$70–80 | ~$20 |
| **Total** | | **~$325** | **~$160–190** |

Scale linearly with the number of clips. Check before costing native vertical that the
video model's schema accepts a 9:16 aspect ratio; otherwise generate vertical keyframes and
crop a 16:9 render, or upscale a centre crop and accept the softness.

## Efficiency targets

- Drafts per clip used: ≤ 3.5 (reference: 5.9).
- Finished clips not used: 0 (finish only after Gate 2).
- Claude cost per finished second of hero: < $3 (reference: $3.41).
- Report both in the delivery note.

## Timeline (reference run, Pacific time)

| Clock | Phase |
|---|---|
| 0:00 | Brief arrives; analysis + concept workflow starts; tooling and a $1 smoke test |
| +1:00 | Concept chosen |
| +1:00–1:25 | Cast, set, first shot drafted and finished; likeness filter solved |
| +1:25–3:05 | 10 shot agents in parallel; music, SFX and compositor built alongside |
| +3:05–3:20 | 25 finishes in parallel |
| +3:30–4:00 | Cuts v1→v5 with 3 AI critique rounds; first master delivered |
| later | 5 human revision rounds, ~1.5 h of work |

With the three gates, add the user's review time at Gate 1 (keyframes) and Gate 2
(animatic). Expect the human revision rounds to shrink, because sound and picture notes
arrive before finishing rather than after delivery.
