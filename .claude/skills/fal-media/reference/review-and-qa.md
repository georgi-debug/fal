# Review and QA

Generation is cheap to repeat and expensive to ship wrong. Separate the maker from the
checker, and verify every claim — including your own.

## The QA bar for a video draft

Pass only if all hold:
- identity and wardrobe match canon (check small accessories: an earring became a cross)
- no warped hands, faces or limbs; no extra fingers
- no text, gibberish signs or logos in frame
- the grade matches the project look (milky shadows, no clipped whites, palette)
- the planned action happens and reads inside the slot length
- motion is smooth, with no morph-melting beyond the intended change
- motion direction agrees with the camera (a forward walk under a faster pull-back reads
  as walking backwards — AI review missed this; a human caught it)
- it will cut with the planned transitions on either side

"Conditional pass with post fixes" is a valid verdict: log the usable seconds, the fix
(retime, matte, grade, trim) and a backup take.

## How to look

1. `scripts/contact_sheet.py clip.mp4 sheet.jpg --fps 4` — timestamped frames across the clip.
2. Full-resolution frames at the moments that matter
   (`scripts/extract_frames.py --at 2.5 clip.mp4 out.png`): faces, hands, props.
3. Transitions in the cut: step through them at 12 fps.
4. After any retime: check for ghosting (frame blending) — sample the nearest frame instead.

## Independent review before money

The reference workflow ran three stages per shot group: produce → an independent strict
reviewer (did not make the shot) → one retry round. Then a director pass reviewed every
pick before any 1080p finish. Reviewers scored most drafts 4–7.5/10; most issues were
fixable in the keyframe.

## AI critique of a cut

Run through fal (`openrouter/router/video`, Gemini 3.1 Pro, `reasoning: true`,
temperature 0.2) on a 720p proxy **with sound**. Ask for four lenses, each with a 1–10
score and timestamped issues (severity 1–5, fix):

```
You are reviewing a [LENGTH] commercial for [BRAND] ([one line on what the brand is]).
It was made with AI generation plus motion graphics. [Two sentences on the concept, the
look and the typography.] Watch the whole video with sound, carefully, more than once.
Review it through four lenses: (1) creative director, (2) VFX and finishing QC,
(3) editor and sound supervisor, (4) [BRAND] brand lead.
For each lens return JSON: {"score": 1-10, "issues": [{"t": seconds, "severity": 1-5,
"issue": "...", "fix": "..."}]}.
```

Treat the output as **leads**. On the reference project:
- a later cut that carried all the earlier fixes scored lower than the cut before it;
- "melting faces", "a different person", "the logo is a cross" and "the prompt is cut off"
  were all false positives on inspection;
- real, applied fixes were: legibility of small type, motivating a transformation, a
  shorter shot, louder effects, more space before the end line.

Verify each claim frame by frame before acting; tell the user which you rejected and why.

## Audio: you cannot hear it

Say so to the user up front, and ask for a human listen in the first hour — four of five
revision rounds on the reference project were about sound (a cue they disliked, a
"jingle" ending, an audible loop, a new sound under the logo).

Objective checks you can run:
- loudness and true peak of the mix (target −14 LUFS integrated, −1.5 dBTP);
- per-cue audibility: is each key effect within ~6–10 dB of the music at its moment?
- beat grid of the score vs the cut points; energy at the planned hits.

AI listening jury (`openrouter/router/audio`, Gemini 3.1 Pro, `reasoning: true`,
temperature 0.1): score quality, mood, hook, artifacts, dead air, unwanted instruments,
copy risk, best 60 s window, overall. **Never name the reference song in the prompt** —
naming it primed the model to hear resemblance everywhere.

## Originality of music

An AI A/B "soundalike" check is not usable: it rated unrelated EDM and trap controls 8–9/10
similar to the reference. Use an objective cover-song detector instead (key-invariant,
beat-synchronous chroma with recurrence-quantification alignment; the fal-commercial skill
ships one) and **calibrate it**:
- positive controls: the reference pitch-shifted and time-stretched (scored 70.6), a
  segment of the reference (44.2);
- negative controls: unrelated tracks (6–12).
A candidate is clear when it scores like the negative controls (the chosen score: 6.48).

## Taste rules learned from the human reviewer

These came from real notes on the reference production; they are good defaults:
- no new sound effect under the logo; let the music fade
- no ending "jingle": play the score straight through and fade over ~5 s
- no effect that contradicts the idea's physics (ice where the idea is water)
- no decorative overlay that adds no meaning (doodles, floating particles)
- never let a walking subject appear to move backwards
- prefer the simpler fix: re-trim before regenerating
