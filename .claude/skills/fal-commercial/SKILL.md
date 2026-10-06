---
name: fal-commercial
description: Runbook for producing a short commercial or brand film (15–90 s, plus 30 s / 15 s / 9:16 cutdowns) end to end with fal generative models through the fal MCP and local post-production scripts — reference analysis, competing concepts judged by AI, cast and keyframes, parallel shot agents making cheap drafts, review gates, 1080p finishing, original music and SFX, a data-driven edit, mix and delivery. Use when the user asks for an ad, spot, promo, brand film, product video or "a commercial like X". Builds on the fal-media skill; read that first.
---

# fal commercial production

Read `../fal-media/SKILL.md` first: its budget ledger, MCP call pattern, prompt patterns,
likeness-filter rules and QA bar apply to every step here.

This runbook reproduces — and tightens — a real production: a 60 s, 1080p fal × Lush Valley
spot whose first cut was delivered unattended 4 hours after the brief for $538.65
($333.92 fal + $204.73 Claude). The changes from that run are marked **[improved]**: they
address where it lost money or needed a human afterwards.

## Shape of the job

```
Brief ─► Measure references ─► 4 concepts ─► 3 AI judges ─► Director synthesis
     ─► Cast + first shot ─► GATE 1: keyframes ─► Parallel shot agents (480p drafts)
     ─► Independent review ─► GATE 2: animatic (all cuts locked on drafts)
     ─► Finish winners at 1080p ─► Music + SFX ─► Edit (cut list) ─► Mix
     ─► AI critique (leads) ─► GATE 3: human eyes and ears ─► Revisions ─► Deliver
```

The three gates are **[improved]**: the original ran with no human checkpoint until the
first cut, then needed five revision rounds, four of them about sound.

## Before you start

1. Fill the brief with the user: `reference/brief-template.md`. Do not start generation
   with open questions on deliverables, budget, brand assets, music rights or who approves
   each gate.
2. Set the budget: `FAL_BUDGET_CAP` ~3% under the generation budget; plan per-group caps.
   Report Claude cost separately — on the reference run it was 38% of the total.
3. Create the project folder:
   ```
   refs/  plan/  kf/  gens/  music/  sfx/  comp/  deliverables/  fal_ledger.jsonl
   ```
4. Confirm the MCP is connected (`search_models` returns results). If not, tell the user.

## Phase 1 — Measure the references (no fal spend)

Turn every reference into numbers before anyone writes a concept:
`reference/reference-analysis.md`. Outputs: `plan/beats.json` (each beat to 0.1 s:
camera, action, effect, device, how the cut is hidden), `plan/style.json` + `STYLE_BLOCK.txt`
+ `NEGATIVE.txt`, `plan/audio.json` (BPM, downbeats, hit points), `plan/brand.json`, and a
type spec measured from reference pixels. Run these as parallel agents.

## Phase 2 — Concepts and judging (no fal spend)

Four concept agents, one angle each (faithful, device-driven, brand-native, showstopper),
same context, full timed beat sheets. Three judge agents with different lenses (creative
director, AI-VFX producer, the reference's editor) score 5 criteria × 10 and list ideas to
graft. A director agent takes the winner, grafts the best losing ideas and fixes the named
weaknesses → `plan/final.json`. Details and prompts: `reference/concepts-and-judging.md`.

Then show the user the winner, the scores and the grafts. **[improved]** Let them veto
before casting.

## Phase 3 — Shot plan

`plan/BEATS.md` / `beats_final.json`: every beat with its reference equivalent, shot,
transformation, camera, transition in, on-screen text, generation plan and **cutdown tags**
(which beats survive in the 30 s, 15 s and vertical versions) **[improved]**.
Rules: `reference/beat-sheet.md`.

- Structural cuts sit on a beat grid that is a whole number of frames (120 BPM = 12 frames
  at 24 fps).
- Everything that must be exact is post: whips, rolls, titles, captions, cursor/UI, logo.
- Write `plan/PRODUCTION.md`, the bible every shot agent reads first
  (`reference/shot-agents.md` has the template).

## Phase 4 — Cast, set and the first shot

1. Character and set sheets with Nano Banana Pro edit (2K, 16:9, 2 per call).
2. Make the **first shot** (keyframe → 480p draft → review) before locking the character.
   Adopt the face the video model settles on; rebuild the sheet from that shot's real
   frames only.
3. Solve the likeness filter here, on one shot, and write the working recipe into
   PRODUCTION.md.
4. Finish this one shot at 1080p as the quality reference for everyone.

## GATE 1 — Keyframes **[improved]**

Every clip's first frame (and last frame for transformations and chained joins) is made
and checked against the QA bar **before any drafts**. Show the user a keyframe sheet; most
failed drafts on the reference run inherited their fault from the keyframe.

## Phase 5 — Parallel shot agents (480p drafts)

Split the shots into ~6–10 groups by location and continuity. One agent per group, each
with its group cap, the ledger, PRODUCTION.md and the beat sheet. Each makes 2 drafts per
clip (up to 2 more after revising), self-reviews with contact sheets and full-res frames,
and returns: the chosen draft, usable seconds, a backup, the exact prompt, QA notes and
post needs. Agents never call the finishing endpoint or `/us/` endpoints.
Then an **independent reviewer** pass, then one retry round with a small extra budget.

Keep agent briefs tight **[improved]**: parallel agents re-reading big context cost
$140.70 of the $204.73 Claude spend. Give each agent only its beats, the bible and the refs
it needs. Template: `reference/shot-agents.md`.

While they run, the main session builds the music, SFX and the edit skeleton.

## GATE 2 — Animatic on drafts **[improved]**

Cut the 60 s **and every cutdown** from the 480p drafts with temp music using the edit
scripts. Lock picture here with the user. Only then finish — and finish only the clips the
locked cuts use, plus backups for fragile ones. (The reference run paid $35.48 for finished
clips that never made the cut.)

## Phase 6 — Finish at 1080p

`draft/complete` for each winner, in parallel. If one fails with "Invalid parameters" on a
valid request, retry once under a new tag, then finish the backup take. Download, settle,
and re-cut on the finished clips (the motion is the same, so the edit holds).

## Phase 7 — Music and SFX

Lyria takes with a timestamped section map on your cut points; several rounds of ~10;
objective structure checks; AI listening jury; objective originality check with controls.
SFX as one batch. Details: `../fal-media/reference/prompting.md` §5–6 and
`../fal-media/reference/review-and-qa.md`. If cutdowns need their own music, generate 30 s
and 15 s takes with their own section maps (they are $0.10 each).

## Phase 8 — Edit, type, mix

Data-driven and versioned: write `comp/cutlist.json`, render with `scripts/render.py`,
mix with `scripts/mix.py`, mux. Each revision is an edit to JSON and a re-render; keep every
version (`cut_v1…`, `mix_v1…`). Format and commands: `reference/cutlist-format.md`.
Craft rules (transitions, type calibration, logo hand-off, retiming, loudness):
`reference/edit-and-finish.md`.

## Phase 9 — Review and GATE 3

1. Your own checks: contact sheets of the cut, every transition at 12 fps, faces and hands
   at full res, loudness and per-cue audibility.
2. AI critique, 2–3 rounds, as leads only (`../fal-media/reference/review-and-qa.md`).
3. **Human eyes and ears** **[improved]**: deliver a review cut with a short list of what
   you are unsure of — anything you could not hear, faces that drift, effects that may not
   read. Ask for audio notes explicitly.
4. Apply notes as cut-list and mix edits. Prefer re-trimming to regenerating; ask before
   spending on regeneration.

## Delivery

- Master: 1920×1080 (and 1080×1920 for vertical), H.264 high bitrate, AAC 320k 48 kHz,
  −14 LUFS integrated, −1.5 dBTP. A 720p review proxy.
- The final cut list and mix JSON, the ledger report, and a one-page production note: what
  was generated with which models, total spend (fal from the ledger, Claude separately, both
  labelled as list-price estimates), and anything the user should check before publishing
  (rights in the references, likeness, platform AI-content labelling).
- Never cut b-roll for explainers from the finished master: it carries burned-in type.

## Rights and guardrails

- A reference ad may inform pacing and structure, not be copied shot for shot. Tell the
  user if the plan tracks one reference closely and suggest legal review before release.
- No copyrighted music, in prompts or as model input. Original score only.
- No real people's likeness; no children; no competitor UI or wording.
- "Made on fal" style claims only when every generated frame and sound really came from fal.

## Files

- `reference/brief-template.md` — the brief to fill with the user
- `reference/reference-analysis.md` — measuring beats, cuts, tempo, grade and type
- `reference/concepts-and-judging.md` — concept angles, judge lenses, rubric, synthesis
- `reference/beat-sheet.md` — beat fields, grid rules, cutdown tagging
- `reference/shot-agents.md` — PRODUCTION.md template and the shot-agent brief
- `reference/edit-and-finish.md` — transitions, typography, logo hand-off, retiming, mix
- `reference/cutlist-format.md` — cut-list and mix JSON, render/mix/mux commands
- `reference/budget-and-schedule.md` — cost model for hero + cutdowns, timeline
- `scripts/` — renderer, typography, mix and music-analysis tools (see cutlist-format.md)
