# Shot agents

Ten parallel shot agents made the reference production in about 1.5 hours. They were also
its largest Claude cost ($140.70 of $204.73), almost all of it from re-reading context.
Keep each agent's context small: its beats, the bible, its refs. Nothing else.

## Grouping

Group clips by location and continuity so one agent owns every join it must match
(e.g. G4 = the bench gags TA1, TA2, TB; G10 = the four finale clips that chain on shared
keyframes). 2–4 clips per group. Give each group a cap: the reference used $15–24 per group
on drafts and keyframes, plus a retry round of +$7.

## PRODUCTION.md (the bible) — template

```markdown
# PRODUCTION — read this first. It overrides the beat sheet where they differ.

## Canon
- Character refs (use ONLY these): refs/cast/<files>. Real frames from the first shot are the
  strongest identity anchors.
- Set refs: refs/set/<files>. Style frames: refs/style/<files>.
- Wardrobe and identity details that must hold: [list].

## Tools (all paid calls go through the ledger)
1. Keyframes: fal-ai/nano-banana-pro/edit — image_urls = uploaded refs, aspect_ratio 16:9,
   resolution 2K, num_images 2. Prompt opens with [LOOK LINE], names refs "the woman from
   image N", ends "No text, no signs, no logos." Save kf/K-<CLIP>a.png (first), b.png (last).
2. Drafts: bytedance/seedance-2.5/image-to-video — image_url, prompt, duration "4"–"8",
   draft: true, generate_audio: false, optional end_image_url.
   NEVER call draft/complete or any /us/ endpoint. The director finishes winners.
3. Ledger: tags start with your group prefix (G3_...). `ledger.py reserve` before every
   submit; stop and report if it refuses. Retries need new tags.
4. Review: contact_sheet.py on every draft; full-res frames of faces, hands, props.

## Prompting
[the video template from fal-media/reference/prompting.md §1] + STYLE_BLOCK verbatim.
Generate slot length + ≥0.5 s handles. Locked-off or slow moves are safest.

## Likeness filter
[the recipe that worked on the first shot; extras' faces hidden in every keyframe]

## QA bar (strict)
[the bar from fal-media/reference/review-and-qa.md]. 2 drafts per clip, then up to 2 more
after revising the keyframe or prompt. Report honestly: "best but flawed" is useful.

## Return, per clip
- chosen draft tag + the seconds to use (in/out)
- backup tag
- keyframe paths
- the exact prompt
- QA notes (what is wrong, even if passed)
- post needs: retime, matte, grade, overlay, transition notes

## Boundaries
Write only to kf/, gens/ and plan/agents/<GROUP>_notes.md.
```

## Agent brief (what you send each shot agent)

```
You are shot agent <GROUP> for <PROJECT>. Read plan/PRODUCTION.md first; it overrides
everything else. Your clips: <CLIP list with beat ids, timecodes, slot lengths>.
Your beats (verbatim from the beat sheet): <only these beats>.
Your cap: $<N> (FAL_GROUP_CAPS is set; the ledger will refuse beyond it).
Make keyframes, 2 drafts per clip, self-review, revise once if needed, and return the
per-clip report in PRODUCTION.md's format. Do not finish to 1080p.
```

## Workflow

1. Produce: all groups in parallel.
2. Review: an independent strict reviewer per group (it did not make the shots), scoring
   each pick /10 with reasons. Most reference drafts scored 4–7.5: "conditional pass with
   post fixes" was common.
3. Retry: one round for failed clips, with a small extra cap.
4. Director pass (you): review every pick before any finishing spend. Check small
   accessories (an earring became a cross), extras' hands, and motion direction.

While the agents run, watch their notes and the ledger, review drafts as they land, and
build the music, SFX and edit skeleton in parallel.
