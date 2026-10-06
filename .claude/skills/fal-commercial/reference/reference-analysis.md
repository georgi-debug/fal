# Measuring the references

Run as parallel agents with no fal spend. Every concept writer and shot agent works from
these numbers, not from impressions.

## Structure reference (the ad whose pacing you borrow)

Split the video into overlapping windows (e.g. 0–20.5, 19.5–40.5, 39.5–60.2 s), one
agent each:

1. Extract frames at 4–6 fps, plus native-rate bursts around suspected transitions; build
   contact sheets (`../fal-media/scripts/contact_sheet.py`).
2. Detect cuts: `ffmpeg -i ref.mp4 -vf "select='gt(scene,0.12)',showinfo" -f null -`, plus
   frame-difference and histogram scans to catch **hidden** cuts (inside whip pans, under
   light leaks, in a dark rolling move, behind a blackout).
3. Log each beat to 0.1 s: camera, action, effect, creative device, and how the cut is hidden.

Output `plan/beats.json`:

```json
{"id": "B06", "t_start": 6.9, "t_end": 10.1, "camera": "steadicam follow behind her",
 "action": "walks the aisle; passengers dance", "effect": "light colour changes on the beat",
 "device": "relight", "cut_in": "continuous", "hidden_cut": null, "caption": true}
```

The reference run found 52 beats and hidden cuts at, e.g., 5.42→5.46 (under a light leak),
10.30→10.34 (inside a whip pan), 39.29–39.46 (dark rolling camera), 43.54 (blackout).

## Music of the reference (structure only — never reuse it)

With librosa (or `scripts/music_analyze.py` if installed): tempo, downbeats, hit points,
and each cut's frame offset from the nearest beat. The reference ran at 107.7 BPM with cuts
1–2 frames before the beat, exits on off-beat "and"s and big moments on bar downbeats.

Then choose your own tempo so a beat is a whole number of frames: 120 BPM at 24 fps =
12 frames per beat, 48 per bar. Check that your grid lands near the reference's structural
hits (the reference run landed all 9 within ~0.1 s; a 96 BPM grid drifted 0.35–0.6 s).

## Look reference (the world)

1. Pick 10 clean, **text-free** frames (inspiration videos often carry burned-in captions;
   take frames from the source footage instead).
2. Measure the grade per frame: 0.5th-percentile luma (milky blacks: 15–40/255),
   99.5th-percentile luma (no clipped whites: 170–233), mean saturation (0.31–0.42), hue
   of greens/skies, vignette or not.
3. Write `plan/style.json` (an ~18-colour palette with names), `STYLE_BLOCK.txt` (prompt
   text, see `../fal-media/reference/prompting.md` §3) and `NEGATIVE.txt`.

## Typography reference

Measure from white-pixel masks of a frame with the target layout: cap height, stem width,
baseline, the ink's left and right extents, colour, shadow. The reference run measured a
title at 149 px cap height with a 29 px stem centred on y≈540, and captions at 53 px cap
height with left ink at x=60 and right ink ending at x≈1860. Then fit your font **by ink**
(cap height and stem within ~2 px), with the weight set explicitly. Matching width alone
picked the wrong weight on the first try.

## Brand

Positioning, wordmark rules, copy options, the end-card asset's timing (frame 0 at rest?
when does motion start and settle?), and the font's weight axis → `plan/brand.json`.

## Find the endpoints

For every capability the plan assumes (relighting, video-to-video, lip sync, 3D), check it
exists before concepts depend on it: `search_models`, `get_model_schema`. The reference plan
assumed a relighting endpoint that did not exist; the light chase became graded presets in post.
