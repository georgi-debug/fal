---
name: fal-media
description: How to generate images, video, music and sound effects with fal models through the fal MCP server (search_models, get_model_schema, get_pricing, submit_job, check_job, get_job_result, upload_file) without overspending or wasting jobs. Use for any fal generation task — a single image, a batch of keyframes, image-to-video clips, a music score, a sound-effect bank — and as the foundation for the fal-commercial skill. Covers budget caps and the cost ledger, the cheap-draft-then-finish pattern, prompt patterns that held up in production, the Seedance likeness filter, and how to review outputs.
---

# fal media generation

You generate media by calling fal models through the fal MCP server. The MCP gives you
the models; it does **not** give you a budget cap, a cost record, a review step or any
editing. This skill supplies that discipline. Follow it for every paid call.

Everything here was learned on a real production (a 60 s commercial: 129 video drafts,
26 finished 1080p clips, 363 images, 30 music takes, 36 SFX for $334 of fal spend).
Model names and prices are as of October 2026: **always confirm them with the MCP before
use** (`search_models`, `get_model_schema`, `get_pricing`).

## The non-negotiables

1. **Know the price before you submit.** Call `get_pricing` for any endpoint you have not
   priced this session, then reserve the cost in the ledger (`scripts/ledger.py reserve`).
   If the ledger refuses, stop and tell the user. Never work around the cap.
2. **Never resubmit to check progress.** Every `submit_job` / `run_model` call is a new
   billable job. Poll with `check_job`, honour `poll_after_seconds`, then `get_job_result`.
3. **Read the schema before the first call to any endpoint** (`get_model_schema`). Do not
   guess parameter names; a wrong one can silently be ignored or fail the job.
4. **Explore cheap, finish only winners.** Draft at the lowest useful resolution, review,
   then pay for full quality only on the takes you will use (see "Draft, review, finish").
5. **Never ask a generative model for anything that must be exact.** Titles, captions,
   logos, UI, cursors, whip pans, barrel rolls and transitions are made in post, in code.
   Every generation prompt ends with "No text, no captions, no logos."
6. **Settle every job in the ledger** — `ok`, or `refunded` for content-policy blocks and
   failed jobs (they are not billed). A reservation never settled counts as spent.
7. **Keep a backup take** of anything that matters. Finishing calls can fail on valid input.

## The call pattern (MCP)

```
search_models / recommend_model      -> pick an endpoint (once per need)
get_model_schema(endpoint_id)        -> exact input names, enums, limits
get_pricing(endpoint_id)             -> unit price; then estimate the job
scripts/ledger.py reserve ...        -> refuses if over the cap
upload_file(...)                     -> for local inputs (keyframes, audio refs)
submit_job(endpoint_id, input)       -> request_id   (use run_model only for fast image jobs)
check_job(endpoint_id, request_id)   -> wait poll_after_seconds; repeat until done
get_job_result(endpoint_id, request_id) -> output URLs; download them immediately
scripts/ledger.py settle --status ok|refunded
```

Details, upload flow and error handling: `reference/mcp-workflow.md`.

**Parallelism:** submit a batch of independent jobs first, then poll them together.
Video jobs take minutes (Seedance 480p draft median 136 s, 1080p finish 186 s), so a
serial submit-and-wait loop wastes most of the wall clock.

**If the MCP is unavailable** (e.g. a 401 from the server), `scripts/falgen.py` runs the
same jobs through the fal Python client with `FAL_KEY`, sharing the same ledger and cap.
Tell the user the MCP failed; do not silently switch accounts or keys.

## Budget and the ledger

`scripts/ledger.py` is an append-only JSONL ledger with a hard global cap and optional
per-group caps, safe for parallel agents (file-locked).

```bash
export FAL_BUDGET_CAP=485                 # set below the real budget
export FAL_GROUP_CAPS="G1:16,G2:18"       # optional, per shot group (tag prefix)
python3 scripts/ledger.py estimate bytedance/seedance-2.5/image-to-video --duration 5 --draft
python3 scripts/ledger.py reserve --tag G1_A2_d1 --endpoint bytedance/seedance-2.5/image-to-video --duration 5 --draft
python3 scripts/ledger.py rid     --tag G1_A2_d1 --request-id <id>   # right after submit_job
python3 scripts/ledger.py settle  --tag G1_A2_d1 --status ok
python3 scripts/ledger.py report --by endpoint
```

- Exit codes: 2 = over a cap (nothing written: stop and report), 3 = unknown endpoint
  (price it with `get_pricing`, then `reserve --est <dollars>`), 4 = duplicate or already
  settled tag, 5 = unknown tag. `--ledger PATH` goes before the subcommand.
- Settle statuses: `ok`; `refunded` or `failed` (not billed: cancels the reservation);
  `charged` (failed but billed).
- Tags are unique: a retry gets a new tag (`_d2`, `_r1`). Prefix tags with the group.
- Set the cap ~3% under the user's budget so in-flight jobs cannot overshoot it.
- Report spend to the user at each milestone, from `report`, not from memory.
- The ledger holds **estimates at list price**, not invoices. Say so when you report.

## Draft, review, finish

The single biggest saving. For Seedance 2.5 (verify the current flags in the schema):

| Step | Endpoint | Key input | Cost (Oct 2026) |
|---|---|---|---|
| Draft | `bytedance/seedance-2.5/image-to-video` | `draft: true`, `duration` "4"–"8", `generate_audio: false`, `image_url`, optional `end_image_url` | ~$0.21 per second (480p) |
| Finish | `bytedance/seedance-2.5/draft/complete` | `draft_id` from the draft, `resolution: "1080p"` | ~$1.14 per second |

The finish keeps the draft's seed and motion, so what you approved is what you get.
Avoid the `/us/` endpoints: they cost ~20% more and have no draft mode.

Rules that saved money:
- **Approve the first frame before drafting.** Most failed drafts inherited the fault from
  the keyframe (wrong grade, loose prop, a malformed earring). Fix the keyframe, not the prompt.
- **Cap drafts per clip** (2 first, up to 2 more after revising). The production averaged
  ~5.9 drafts per clip used; ~3.5 is a realistic target.
- **Lock the edit on drafts, then finish.** $35 of finished 1080p clips were never used
  because they were finished before the cut was locked.
- **Generate the slot length plus ~0.5 s of handles** on each side; you will retime.
- **Re-trim before regenerating.** A motion problem can often be fixed with a different
  in-point or speed. Regeneration attempts on one shot cost $11 and produced nothing usable.

## Prompting

Full templates in `reference/prompting.md`. The video pattern that held up for every clip:

```
One continuous smooth shot, no cuts.
[camera: framing, move, speed]
[subject: identity and wardrobe details that must hold]
[timed actions: "at about 1.5 seconds ...", "from 3 seconds ..."]
[ONE calm, physically real impossible event]
[background behaviour: "the passengers never look up"]
[STYLE BLOCK, verbatim, identical in every shot]
No text, no captions, no logos.
```

- Keyframes (Nano Banana Pro edit): pass reference images and name them in the prompt
  ("the woman from image 2"); open with the film-stock/look line, end with "No text, no
  signs, no logos."
- Use `end_image_url` for controlled before→after changes and to chain clips (a clip's
  last frame becomes the next clip's first frame: `scripts/extract_frames.py`).
- A transformation one clip cannot make: design a **middle keyframe** and chain two clips.
- Avoid shedding verbs ("petals flutter off"): they produce debris and dissolving props.
- One prompt style block and one negative block per project, pasted verbatim everywhere.

## People and the likeness filter

Seedance rejects input frames whose faces look like real people
(`content_policy_violation` / `partner_validation_failed`). Rejections are **free** and
the filter is **random**: identical inputs both passed and failed. Plan for it:

- Hide extras' faces in every keyframe: hats tipped down, raised books, backs to camera,
  soft focus, or animal / material characters.
- Soften close faces before submitting: `scripts/soften_face.py --preset close`
  (1.2 px blur, 8% milky lift, grain), or bake a bloom flare over the face (`--preset bloom`).
- Generate the first shot before locking a character design; then rebuild the character
  sheet only from that shot's real frames, or the old design pulls it back.

Recipes and measured pass rates: `reference/likeness-filter.md`.

## Review

Never judge a clip from one frame. For every draft:

1. `scripts/contact_sheet.py clip.mp4 sheet.jpg` — timestamped frames across the clip.
2. Full-resolution frames of faces, hands and small accessories (they mutate).
3. Check the motion direction against the camera move (a forward walk under a faster
   pull-back reads as walking backwards).

AI critique (a video or audio model run through fal) is a source of **leads, not verdicts**:
verify each claim frame by frame, and calibrate any AI comparison with unrelated controls.
You cannot hear audio: say so, and get a human to listen early. Details:
`reference/review-and-qa.md`.

## Music and sound effects

- Score: many cheap takes beat one expensive one. Put a **timestamped section map** matched
  to your cut points in the prompt (`[0:12] first big lift ...`). Pick a tempo whose beat is
  a whole number of frames (120 BPM = 12 frames at 24 fps).
- Never name a copyrighted reference song, artist or lyrics in a prompt, and never feed
  its audio to a music model. Check originality objectively (see review reference).
- SFX: generate a whole bank in one batch; beds with `loop: true`. Peak-normalise each
  SFX before mixing — source levels varied by 48 dB.

Endpoints, settings and price estimators for every model used:
`reference/models-and-settings.md`.

## Files

- `scripts/ledger.py` — budget cap, cost estimates, ledger, report
- `scripts/falgen.py` — Python-API fallback runner sharing the ledger
- `scripts/contact_sheet.py` — timestamped contact sheets for review
- `scripts/extract_frames.py` — first / last / at-time frames for chaining and QA
- `scripts/soften_face.py` — likeness-filter softening presets
- `reference/mcp-workflow.md` — tool-by-tool MCP usage, uploads, polling, errors
- `reference/models-and-settings.md` — endpoints, settings, prices, gotchas
- `reference/prompting.md` — video, keyframe, style-block, negative and music templates
- `reference/likeness-filter.md` — filter behaviour and every recipe that passed
- `reference/review-and-qa.md` — QA bar, AI critique, music judging, originality check

For a full commercial or brand film, use the `fal-commercial` skill on top of this one.
