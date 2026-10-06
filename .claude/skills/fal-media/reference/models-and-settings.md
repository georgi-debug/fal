# Models, settings and prices

What was actually used on the reference production (October 2026), with the settings that
worked. **Prices drift and models get new versions:** confirm with `get_pricing` and
`get_model_schema` before use, and prefer a newer model only after a cheap test.

## Video

### Seedance 2.5 image-to-video (the workhorse)

| | Draft | Finish |
|---|---|---|
| Endpoint | `bytedance/seedance-2.5/image-to-video` | `bytedance/seedance-2.5/draft/complete` |
| Input | `image_url`, `prompt`, `duration` "4"–"8" (string), `draft: true`, `generate_audio: false`, optional `end_image_url` | `draft_id` (returned by the draft), `resolution: "1080p"` |
| Output | 480p clip + `draft_id` | 1080p clip, same seed and motion |
| Median time | 136 s | 186 s |
| Price | tokens = w × h × s × 24 / 1024 at $0.0214 per 1k tokens → ~$0.21/s | $0.0234 per 1k tokens at 1920×1080 → ~$1.14/s |

- Of 172 shot-agent draft requests: 89 at 4 s, 53 at 5 s, 28 at 6 s; 102 used an end frame.
- Avoid `bytedance/seedance-2.5/us/*`: ~20% dearer, no `draft` option, and a native US
  1080p take lost to a non-US 480p draft completed to 1080p.
- Only image-to-video was used. Reference-to-video with face references was judged a
  blocking risk for the likeness filter; identity came from keyframes instead.
- `generate_audio: false` always: sound is generated and mixed separately.

## Images

| Endpoint | Use | Settings | Price |
|---|---|---|---|
| `fal-ai/nano-banana-pro/edit` | Keyframes, character and set sheets, before/after pairs, matte plates, sprites | `image_urls` (refs), `aspect_ratio: "16:9"`, `resolution: "2K"`, `num_images: 2`, png | $0.15 per image (4K ×2) |
| `fal-ai/nano-banana-pro` | Text-to-image when there is no reference | as above | $0.15 per image |
| `fal-ai/nano-banana-2/edit`, `bytedance/seedream/v5/pro/edit` | Cheaper alternatives listed in the runner (not tested in production) | – | $0.08, $0.0675 |
| `openai/gpt-image-2.5/sunburst/text-to-image` + `/edit` | Explainer/diagram art (not for frames that must match a look) | – | ~$1 per job |

Keyframe techniques that worked:
- **Before/after pairs as edits of one frame**, so they stay pixel-aligned (floor → pond).
- **A relit copy of the previous clip's last frame** as the next clip's first frame: the
  cut hides at the peak of a light bloom.
- **Designed chains:** the last frame of clip N *is* the first frame of clip N+1.
- **Elements on black** (petals, down) animated separately and composited — usable, but
  decorative layers were the first thing cut in review.

## Music

| Endpoint | Verdict | Notes |
|---|---|---|
| `google/lyria-3.5` | **Use.** $0.10 a take | Takes a prompt and optional `image_url` (a style frame). Ignores length requests (returned 62–152 s for "exactly one minute"), takes 1–9 min, returns a `lyrics` timing map even for wordless vocals. Its content check is random: two prompts were refused that passed unchanged in other takes (refused = not billed) |
| `elevenlabs/music/v2.5` | Avoid for score | $0.60/min. A timed `composition_plan` gave near-silent intros (−47 to −62 dB) and dead breakdowns (down to −101 dB). `music_length_ms` cannot be combined with `composition_plan` |

Winning approach: 24 Lyria takes over 3 rounds, the last with a timestamped section map
matched to the cut points. Template in `prompting.md`.

## Sound effects

`fal-ai/elevenlabs/sound-effects/v2` — about $0.002 per second. Settings: `prompt_influence: 0.45`,
output format `mp3_44100_192` (check the schema for the field name), 0.5–15 s, `loop: true` for beds (wind, room tone, rail
clack). Generate the whole bank in one batch (35 sounds cost $0.34). Expect some outputs
to come back nearly silent (one bed peaked at −48 dBFS): measure and regenerate.

## AI review models (run through fal)

| Endpoint | Use | Settings |
|---|---|---|
| `openrouter/router/video` | Critique of a cut (720p proxy with sound) | model `google/gemini-3.1-pro-preview`, `reasoning: true`, temperature 0.2; ~$0.30 a call |
| `openrouter/router/audio` | Listening jury for music takes | model `google/gemini-3.1-pro-preview`, `reasoning: true` (mandatory on this endpoint), temperature 0.1 |
| `fal-ai/demucs` | Stem separation | $0.06; rarely needed |

Use the Pro model for listening: a Flash model misheard a flute as a solo violin.

## Estimators (as in `scripts/ledger.py`)

| Item | Estimate |
|---|---|
| Seedance draft (480p, 854×480) | w × h × s × 24 / 1024 tokens × $0.0214/1k ≈ $0.206 per second |
| Seedance finish (1080p) | same formula at 1920×1080 × $0.0234/1k ≈ $1.14 per second |
| Seedance US endpoints | ×1.2 |
| Nano Banana Pro | $0.15 per image (×2 at 4K) |
| Lyria 3.5 | $0.10 per take |
| ElevenLabs Music | $0.60 per minute |
| ElevenLabs SFX | $0.002 per second |
| Video critique | ~$0.30 |

Reference production, for scale: 129 drafts $125.40; 26 finishes $133.07; 363 images
$54.45; 30 music takes $6.00; 36 SFX $0.37; 52 AI reviews $6.44.
