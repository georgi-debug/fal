# Prompt templates

Templates first, then real prompts from the reference production as worked examples.
Swap the bracketed parts; keep the structure.

## 1. Video (Seedance image-to-video)

```
One continuous smooth shot, no cuts.
[CAMERA] Locked-off camera at seated eye height, looking straight across the aisle ...
         | The camera keeps orbiting smoothly clockwise around her at shoulder height ...
[SUBJECT LOCK] The young woman wears a cropped berry-magenta wide-wale corduroy jacket
         with clearly visible ribs and a cream fleece collar ... (repeat the details that
         drift: earrings, hair, the logo on the back)
[TIMED ACTIONS] For the first second nothing moves except ... At about 2 seconds she
         turns her head to the left ... From 3 seconds ...
[ONE IMPOSSIBLE EVENT, CALM AND PHYSICAL] ... wherever the ring passes, the oak boards
         become clear, glassy, still water that mirrors the sky ...
[CONTINUITY GUARDS] The lamb stays the same small size the whole time. The man never
         wakes and never looks up. The framing never moves.
[STYLE BLOCK — verbatim, identical in every shot]
No text, no captions, no logos.
```

Rules:
- One impossible event per clip. If a change needs two steps, design a middle keyframe and
  make two clips (first→middle, middle→last).
- Give timings in seconds. The model follows rough timing well enough to cut on.
- Say what must **not** change ("the framing never moves", "same small lamb throughout",
  "hands in her pockets", "the passengers never look up"). These guards fixed most drift.
- Calm verbs. Avoid shedding and particle verbs ("petals flutter off", "sparkles"): they
  produce debris and dissolving props.
- With `end_image_url`, keep the framing identical between first and last frame when the
  change is in the subject.
- Never ask for fast whips, rolls, titles, logos, UI or cursors. Those are post.

### Worked example: a transformation with first and last frames (A4, 4 s draft)

Keyframes: `K-A4a` (boots on the oak floor) and `K-A4b` (the same frame edited so the floor
is a sky-reflecting pond). Used 1.6 s of it, retimed.

> One continuous smooth shot, no cuts. Looking straight down from above at the woman's
> moss-green suede boots on the worn oak floorboards and sage runner of the railcar aisle;
> the seated passengers' shoes and a wicker basket sit around her. The camera is nearly
> locked off, with a slow gentle drift downward toward her boots. After a moment, a single
> soft ring of ripple spreads outward from beneath her boots across the floor, and wherever
> the ring passes, the oak boards and the runner become clear, glassy, still water that
> impossibly mirrors open pale periwinkle sky and soft white clouds, with smooth stones on
> the bottom. When the ring reaches the edges of the frame the whole floor is a pond: her
> boots stand on a flat weathered stepping stone, the passengers' shoes rest on stepping
> stones, and cosmos and forget-me-nots grow up out of the water along the bench bases and
> sway slightly. No one moves their feet; everything is calm and physical. Plain warm noon
> daylight. [STYLE BLOCK] No text, no captions, no logos.

### Worked example: a two-clip chain through a designed middle frame (TA1 → TA2)

A painted lamb stepping out of a picture failed as one clip (the model turned the painting
into a photo). Fix: a middle keyframe, half real lamb and half paint, used as TA1's last
frame and TA2's first frame.

> TA1: ... Then the painted lamb, still a flat painting facing the same way, lowers its
> head and takes one slow, careful step forward and down out of the picture: only the parts
> of it that pass over the bottom edge of the oak frame — its head, chest and front legs —
> become a real, soft, three-dimensional lamb with real curly wool and real little hooves,
> while its back half stays flat brushy paint inside the picture and never turns or moves.
> ... The lamb stays the same small size the whole time. The man never wakes and never
> looks up.

## 2. Keyframes (Nano Banana Pro edit)

```
Film still from [LOOK LINE: "a dreamy 1970s summer movie, 16mm color negative"].
[COMPOSITION: lens height, framing, where the subject stands, what is in each third].
[SUBJECT from refs: "the woman from image 1, exactly as in image 1: same face, hair,
 jacket ..."; "the railcar interior from image 2"].
[STATE OF THE WORLD at this instant: what has or has not transformed yet].
[EXTRAS: faces hidden — straw hats tipped down, books raised, backs to camera].
[GRADE notes if the model drifts: "milky lifted shadows, no pure black, olive greens"].
No text, no signs, no logos.
```

- Pass identity refs as `image_urls` and name each by position ("image 1").
- After the first video shot exists, use **its real frames** as the identity refs.
- For before/after pairs, generate "after" as an edit of "before" so they stay aligned.
- `num_images: 2` per call is cheap insurance; pick the better one.

## 3. Style block

Write once per project from measured reference frames, then paste verbatim into every video
prompt. It should name: the world, palette, light, composition and camera rules, grade
(shadows, highlights, saturation, grain), wardrobe, and how impossible things behave.
The reference production's block:

> Lush Valley style: a dreamy, handmade 1970s summer-film world. Smooth, rounded
> gumdrop-shaped green hills over a meadow of pink, magenta and white cosmos, oxeye daisies,
> black-eyed susans and hollyhocks; cream-canvas covered wagons with sage-teal bodies and
> daisy garlands; butterflies drifting. High summer sun, warm and soft, backlighting petals
> and hair with gentle halation; pale periwinkle sky fading to a warm haze at the horizon.
> Centered, symmetrical, level-horizon compositions; locked-off or slow, smooth camera
> moves; shallow depth of field on close-ups. Warm sun-faded color-negative grade: lifted
> milky shadows, creamy rolled-off highlights, olive-yellow greens, dusty rose pinks,
> apricot and lavender accents, moderate saturation, soft low-contrast lens, fine delicate
> grain, 24fps. Linen, corduroy, crochet and embroidered folk-vest wardrobe on natural,
> unposed adults. Impossible things are calm, tactile and physically real, as if built
> in-camera. No on-screen text or logos.

Measure the look rather than describe it from memory: e.g. 0.5th-percentile luma (milky
blacks were 15–40/255), 99.5th-percentile luma (no clipped whites: 170–233), saturation
(0.31–0.42). Those numbers also drive the post grade.

## 4. Negative list

If the endpoint's schema has a negative-prompt field, pass a condensed version. Either way,
the negative list is the **QA checklist** for you and your shot agents: check every keyframe
and draft against it. Organise it by: text and branding · light and grade · look (CGI, plastic skin, sparkles,
portals) · camera (handheld, whips, fisheye) · setting (anachronisms) · people (crowds,
twins, celebrities, children, warped hands, identity drift) · wardrobe · audio
(`generate_audio` off).

## 5. Music (Lyria)

```
[GENRE + ERA + USE]: Instrumental-style 1970s European library pop for a dreamy, whimsical TV commercial:
[INSTRUMENTS]: ... nylon-string guitar, Fender Rhodes, vibraphone, harp, flute ...
[EXCLUSIONS]: No piano, no harpsichord, no lyrics.   (exclude whatever the reference is known for)
[PRODUCTION]: Warm analog tape, gentle and joyful.
[LENGTH + GRID]: Exactly one minute long, 120 BPM, 4/4, one continuous escalating journey:
[SECTION MAP, timestamped to the cut points]:
 [0:00-0:05] gentle magical intro ... [0:05.5] bass and light drums enter ...
 [0:12] first big lift ... [0:38.5] the band stops; [0:39.5-0:44] weightless breakdown ...
 [0:49.5-0:58] triumphant finale ... [0:58-1:00] final chord rings out.
```

- Optional `image_url`: a style frame helps the mood.
- Lyria will not hit the length. Pick takes whose structure fits, then cut picture to the
  take's measured beat grid (advance the score so beats land on exact frame multiples).
- Never name a copyrighted song, artist or lyrics; never upload its audio as a reference.
- Run 3–4 rounds of ~10 takes, narrowing the style each round.

## 6. Sound effects

Short, literal, physical prompts with the material and the space: "a single felt-padded
thump on a wooden railcar floor, close, dry", "steady vintage rail clack on jointed track,
interior of a wooden carriage, loopable". Generate 0.5–15 s; mark beds as loopable.
