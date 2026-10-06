# Edit, typography, finishing and mix

The edit is code: a cut list (JSON) rendered frame-accurately by `scripts/render.py`.
Every revision is a JSON edit and a re-render (minutes), and every version is kept.
Formats and commands: `cutlist-format.md`.

## Why not generate transitions or type

On the reference run, whips, rolls, bloom cuts, the title, captions, typed prompt pills,
the cursor and the logo hand-off were all post. They are frame-exact, editable, cheap and
never hallucinate. Generated text and UI would have been none of those.

## Transition catalogue

| Name | What it is | Use |
|---|---|---|
| `cut` | hard cut | on a beat, 1–2 frames before it |
| `dissolve` | crossfade | rarely; reads as soft |
| `whip` | directional smear with pixel offset and blur (e.g. 650 px / 560 blur vertical; 900 / 300 horizontal) | hiding a cut inside camera motion. Vertical whips: less travel, more blur (avoids a mirrored edge band) |
| `bloom_in` | luma/halation ramp on A, hard cut into B whose first frame already carries a decaying bloom | light-motivated cuts; pairs with a bloom baked into B's keyframe |
| `dark_out` | sink to a near-black from the palette (not pure black), hard cut | scene changes, tunnels |
| `roll_dark` | accelerating ~160° roll with rotational blur into dark | big act breaks |
| `reveal` | expanding feathered mask or PNG matte, optional ripple edge | before/after plates registered pixel to pixel |
| `slide_still` | a still (RGBA) crossing frame with motion blur | wipes on a foreground object (a wing, an island underside) |

## Retiming

- Piecewise ramps `[[segment_t, source_t], ...]` to land actions on beats and skip morph
  failures.
- Sample the **nearest frame**; blending frames caused ghosting on sped-up shots.
- True slow motion: pre-render with ffmpeg motion interpolation —
  `setpts=PTS/0.89,minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1`.
- Log usable seconds for every draft so the editor knows what each clip can give.

## Finishing

Global halation (~0.05) and grain (~0.016) **before** graphics, so the type stays clean.
Per-segment grade tweaks to match neighbours. Graded "relight" presets (a grade plus a
coloured directional light ramp) can stand in for a relighting model.

## Typography

- Fit the font to the reference **by ink**: cap height and stem width within ~2 px.
- Set the variable-font weight explicitly. Variable fonts default to their lightest
  instance (the reference font defaulted to 300; the spot used 600 throughout).
- Render at 2× and downsample. Flat white type cut on and off with the picture matched the
  look reference; a soft shadow was added after critique for legibility.
- Reference sizes at 1080p: title 199 px, split captions 67 px, typed tagline 136 px at
  34 characters/s, lockup 104 px, UI pill 38 px.
- **[improved]** Small captions on bright windows were hard to read, and 38 px is too small
  for phones. Put captions on plain areas, keep the shadow, and use ≥56 px for any UI text.
- Typed text: fast enough to finish and hold (pills at 100–110 chars/s); show the caret
  only while typing — a trailing caret was misread as a letter.

## Logo hand-off

1. Take the end-card asset's frame 0 (must be at rest) and extract the mark's masks from it.
2. Bring the mark in rest-to-rest: it lands exactly where frame 0 has it, at the same size,
   with no rotation, then the asset starts at frame 0.
3. Conform the asset's frame rate by dropping frames (30→24), not blending.
4. A story element can become part of the mark (the sun became the hole in the fal ring).
5. No new sound under the logo; let the music fade.

## Mix

`scripts/mix.py` from a JSON cue list:
1. Music: resample 48 kHz stereo, trim/offset so beats land on whole frames, gain, fade out
   (straight through with a ~5 s fade is the safe default).
2. Each SFX: peak-normalise to −3 dBFS (boost capped at +24 dB), then the cue gain, then a
   bus gain (+9 dB on the reference) so key hits sit ~6–10 dB under the music. Beds loop.
3. Sum without normalising (`amix normalize=0`).
4. Two-pass loudnorm: measure, then apply with `linear=true` → −14 LUFS, −1.5 dBTP, LRA 11.
5. Mux with the silent picture: copy video, AAC 320k 48 kHz, `+faststart`.

Build ffmpeg commands as argument lists, not shell strings: the reference run lost time to
shell quoting (`$M:linear` read as a variable modifier) and word-splitting.

Sound design ideas that worked: the ambient bed stops dead for a silent, floating moment;
key taps under typed UI and a soft tick on each click; one loud signature cue on a downbeat;
a felt thump to sell a camera jolt.

## Version discipline

`comp/out/cut_v1_silent.mp4 … cut_vN`, `mix_v1.wav … mix_vN.wav`, each with the cut list /
mix JSON that made it. Never overwrite. The reference run went v1→v8 (picture) and
v1→v9 (mix) and could go back to any of them.
