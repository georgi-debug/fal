# The Seedance likeness filter

The main production constraint on the reference project: 55 blocked requests across 10
shot groups (one group alone had 24). Plan for it from the first keyframe.

## How it behaves

- **What is checked:** only the input frames (`image_url` and `end_image_url`). Faces that
  appear during the generated motion are not checked.
- **Error:** `content_policy_violation` / `partner_validation_failed`, "may contain
  likenesses of real people". Same on US and non-US endpoints.
- **Cost:** none. Blocked requests are not billed. Settle them `refunded` in the ledger.
- **Random:** the identical frame passed twice and failed twice. Logged pass rates: a
  graded frame 0/5 vs the ungraded version 2/4; an end-frame face blur of 1.5 px 0/6 vs
  2.0 px 3/6. Because blocks are free, resubmitting an unchanged input is a valid tactic.
- **Generated faces trip it too.** A face the model invented for an extra resembled a real
  person; the hero character's own generated face was blocked at medium and close sizes.
- **Re-rendering the face** with an image model did not help.

## Diagnose by bisection

When a keyframe is blocked, find which face triggers it: blur one face at a time and
resubmit (free). On the reference project, blurring either of the two largest faces still
failed, blurring every extra passed, and a close-up of the hero alone passed — so the
trigger was an extra, not face size.

## Recipes that passed

`scripts/soften_face.py` implements the presets.

| Recipe | Settings | Use |
|---|---|---|
| Close frontal face (`--preset close`) | resize to 1920 wide, 1.2 px Gaussian blur, 8% milky lift toward RGB(200,185,165), grain σ 6 | Hero close-ups. Usually passes and suits a film look |
| Lost profile (`--preset profile`) | 1280×720, 1.0 px blur, 10% milk, grain σ 7 | Side-on medium shots |
| Local face blur (`--box x,y,w,h --blur 1.7`) | face-only blur; 1.4 px failed, 1.7 and 2.0 px passed | When the rest of the frame must stay sharp. Seedance re-sharpens within ~0.3–0.55 s: skip those frames in the edit |
| Baked bloom (`--preset bloom`) | a warm sun flare over the face in the first frame; the model clears it in ~0.3 s | Doubles as a light-bloom transition |
| Head turned away | first frame shows the back or a 3/4 rear view; the head swings round during the clip | Any shot that can start on a turn |
| Heavy blur on a giant close-up | radius-10 blur on the face only | A face that fills the frame |

## Composition rules for extras

No extra's face clearly visible in any keyframe:
- straw hats tipped down, faces in shadowed profile or turned to the window
- books, newspapers or fans raised in front of faces
- backs to camera, soft-focus background figures
- non-human or material characters (animals in clothes, storybook-painted or handmade
  figures) — these turned a constraint into the spot's charm

If a static prop hides a face, give the person an action (fanning a hat, turning a page)
so they do not read as frozen.

## Character identity

- Generate the first shot before locking the character. The video model will settle on a
  face; adopt it.
- Rebuild the character sheet only from real frames of that shot, with an explicit note of
  what must not change back ("curls, NOT locs, NOT braids"). Older references pull the
  design back.
- Use reference-to-video with face references only after a cheap test: it was judged too
  risky for the filter, and identity held well through image-to-video from approved keyframes.
