# Beat sheet and shot plan

## Beat fields

```json
{
  "id": "B06",
  "t_start": 7.0, "t_end": 10.0,
  "ref_equivalent": "6.9–10.1: follow behind her while the light changes on the beat",
  "shot": "Follow behind the hero down the aisle, sun logo on her back centred",
  "transformation": "Each head turn swings the sun to where she looks",
  "camera": "Steadicam, upper-back height, slow forward creep",
  "transition_in": "continuous",
  "text": {"kind": "split_caption", "left": "Put the sun", "right": "anywhere you like", "on": 7.5, "off": 10.0},
  "clip": "A2",
  "generation": "image-to-video 6 s from K-A2a; post relight presets on 7.5, 8.0, 8.5, 9.0, 9.5",
  "post": ["relight presets", "caption"],
  "cutdowns": ["60", "30", "V"]
}
```

`cutdowns` tags which versions keep the beat: `60`, `30`, `15`, `V` (vertical).

## Grid rules

- Structural cuts on the beat grid (whole frames per beat: 120 BPM = 12 frames at 24 fps).
- Exits on off-beat "and"s; reveals and big moments on bar downbeats.
- Captions only over calm shots, and over plain areas of the frame (panels, sky) — small
  type on bright windows was hard to read in the reference cut.
- Every transition is hidden where the reference hides its own, and has a cause in the
  world (an eyeline, a flare, a whip motivated by a head turn).
- Plan ~0.5 s of handles on each side of every clip.

## Planning cutdowns from day one

A cutdown made by salvaging the 60 s edit is weaker than one planned in. While writing the
beats:

| Version | Must contain | Rule |
|---|---|---|
| 30 s | Hook, 3–4 signature transformations, the device once, the brand payoff | Tag ~10 beats; check they still read in order without the beats between |
| 15 s | Hook, ONE signature transformation, brand payoff | ~5 beats; the payoff must not depend on setup that is cut |
| 9:16 | The 30 s or 15 s beat list | Mark each beat `crop` (centred, one-point compositions) or `native` (wide set-pieces: make a vertical keyframe and draft). Split left/right captions do not fit: stack them |
| 6 s bumper | Hook frame + logo | Optional |

Ask the music model for section maps with clean 30 s and 15 s exits, or generate separate
short takes.

## Generation plan per clip

For each clip: keyframes needed (first, last, middle), duration, which group/agent makes it,
risk notes (faces → likeness filter; thin props; hands), and the fallback if the model
cannot do it (post effect, a split into two clips, a simpler action).

## What changed in production (expect it)

- A planned capability did not exist on the platform → replaced by a post effect.
- A single long "panorama" became separate locked-off clips joined by whips.
- A transformation the model could not do in one clip became two clips through a designed
  middle keyframe.
- Three planned shots were finished and later cut. Lock the animatic on drafts first.
