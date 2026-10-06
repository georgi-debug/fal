# Cut list and mix format

The edit is data. You write `comp/cutlist.json` (picture, transitions, type, graphics) and
`comp/mix.json` (music, SFX, loudness), then render, mix and mux with the scripts in `../scripts/`.
A revision means editing the JSON and re-rendering. Keep every version (`cutlist_v3.json` →
`cut_v3.mp4`, `mix_v3.json` → `mix_v3.wav`).

The scripts are brand-neutral: fonts, colours, logos and end cards all come from the JSON.

| Script | Purpose |
|---|---|
| `render.py` | cut list → frame-accurate silent H.264 master; `--check`, `--stills`, `--from/--to`, `--jobs` |
| `engine.py` | decoder/cache, easing, image ops, grading, transition primitives, encoder (library) |
| `typo.py` | variable-font typography: ink-accurate alignment, 2× supersampling, typed-on text (library) |
| `ui.py` | generic prompt pill and cursor sprite with click spin and pulse (library) |
| `mix.py` | `mix` (JSON → two-pass −14 LUFS master), `measure`, `mux` |
| `slowmo.py` | true slow motion with motion-compensated interpolation (`minterpolate`) |
| `music_analyze.py` | *optional (librosa)*: tempo, beat-grid fit, accents at hit times, section energy |
| `cover_detect.py` | *optional (librosa)*: key-invariant originality check against a reference, with controls |

Requirements: Python 3.9+, `numpy`, `opencv-python-headless`, `pillow`, and `ffmpeg`/`ffprobe` on
PATH. The optional music tools also need `librosa` and `soundfile`.

---

## 1. Commands

Run these from the project root. `S=.claude/skills/fal-commercial/scripts` or wherever the skill lives.

```bash
# validate: timeline table, source ranges incl. transition handles, warnings (gaps, freezes, off-frame times)
python $S/render.py comp/cutlist.json --check

# review stills (PNG) at given seconds -> comp/out/stills/ (or --stills-dir)
python $S/render.py comp/cutlist.json --stills 1.0,4.0,5.5,9.0

# partial render of one passage (frame-exact; t = n / fps)
python $S/render.py comp/cutlist.json --from 3.5 --to 4.5 --out comp/out/whip_test.mp4

# full render (CRF 16), final master (CRF 12), parallel chunks (concatenated losslessly)
python $S/render.py comp/cutlist.json --out comp/out/cut_v1.mp4
python $S/render.py comp/cutlist.json --out comp/out/cut_v1_final.mp4 --crf 12 --preset slow --jobs 3

# transitions at 12 fps / whole cut at 2 fps, as timestamped contact sheets
# (contact_sheet.py lives in the fal-media skill: M=.claude/skills/fal-media/scripts)
python $M/contact_sheet.py comp/out/cut_v1.mp4 comp/out/sheet_whip.jpg --fps 12 --start 3.75 --end 4.25
python $M/contact_sheet.py comp/out/cut_v1.mp4 comp/out/sheet_v1.jpg --fps 2

# mix (premix -> loudnorm pass 1 measure -> pass 2 linear) and check loudness
python $S/mix.py mix comp/mix.json --out comp/out/mix_v1.wav
python $S/mix.py measure comp/out/mix_v1.wav

# mux the silent picture with the mix (video copied, AAC 320k 48 kHz, +faststart)
python $S/mix.py mux --video comp/out/cut_v1_final.mp4 --audio comp/out/mix_v1.wav \
    --out deliverables/spot_60s_1080p.mp4

# 720p review proxy from the master
ffmpeg -i deliverables/spot_60s_1080p.mp4 -vf scale=1280:-2 -c:v libx264 -crf 23 -c:a aac -b:a 160k \
    -movflags +faststart deliverables/spot_60s_720p_review.mp4

# true slow motion of a source clip before cutting it in (0.89x shown)
python $S/slowmo.py gens/E1/out_0.mp4 comp/slow/E1_089.mp4 --factor 0.89
#   = ffmpeg -i IN -vf "setpts=PTS/0.89,minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" OUT
#   slow: ~25-30 s wall per source second at 1080p, so run it once per clip, in parallel, early
```

Every ffmpeg call in the scripts is built as an argument list, so shell quoting can't break it.

**Speed** (4-core cloud VM, 1080p, halation + grain on): about 4.3 s of wall clock per output second
in one process, and 3.3–3.7 s with `--jobs 2–3`. So a 60 s cut takes about 4 min. Short partial
renders cost more per second (5–7 s) because of clip decode start-up. The vertical
1080×1920 test ran at 5.7 s/s.

---

## 2. Conventions

- **Time** is timeline seconds. Frame `n` is rendered at `t = n / fps`. Put cuts on frame boundaries
  (at 24 fps, multiples of 1/24 s; a 0.5 s beat grid is 12 frames); `--check` warns otherwise.
- **Paths** are relative to the cut list's folder, or to `"root"` if set (use `"root": ".."` when
  the cut list lives in `comp/` and paths start at the project root).
- **Pixels** are output-frame pixels. **Colours** are `"#RRGGBB"` or `[r,g,b]` (0–255, or 0–1).
- **Sources** are scaled to cover the frame and centre-cropped by default (`fit`, `anchor`).
  A 30 fps or 720p clip is conformed automatically: nearest frame by time, Lanczos scaling.
- **Sampling is nearest-frame.** Frame blending ghosted on retimes in production. For slow motion
  below ~0.95×, pre-render with `slowmo.py`; `"blend": true` exists but use it only as a fallback.
- **Handles:** a transition straddles the cut at `t` by `hw` seconds each side. A whip, dark_out,
  bloom or dip samples A until `t` and B from `t`. A **dissolve or reveal samples both sides across
  the whole window**, so A plays past its `t1` and B starts before its `t0`. A source time outside
  the clip holds the first/last frame; `--check` prints every segment's source range including
  handles and warns about such freezes.
- Render order per frame: segment or transition → finish (grade, halation, vignette, grain) →
  overlays in list order. Type and graphics therefore sit on top, clean.

---

## 3. Cut list schema

### Top level

| key | default | meaning |
|---|---|---|
| `width`, `height`, `fps` | 1920, 1080, 24 | output raster. Vertical = 1080 × 1920; any size works |
| `duration` | last segment `t1` | render length (an end card may run past the last segment) |
| `root` | `"."` | base folder for relative paths |
| `look.floor` | `#1E1F1C` | the "near-black" that `dark_out`, `roll_dark`, `dip` and `milky` use. Never pure black unless you want it |
| `look.bloom_color` | `#F3ECDF` | colour that `bloom` / `bloom_in` lift toward |
| `fonts` | — | `{name: {"path": "...ttf", "wght": 600, "axes": {...}}}`; `main` is the default font. **Required for any text** |
| `styles` | see §3.5 | named text styles merged over the defaults: `title`, `caption`, `text`, `pill` (add your own) |
| `ui` | — | defaults for `cursor` and `pill` (§3.4) |
| `finish` | — | whole-picture finishing (§3.6) |
| `source_matrix` | `"bt709"` | matrix assumed for **untagged** HD sources (`"bt601"` or `"auto"` to override); tagged sources use their tag |
| `segments`, `transitions`, `overlays` | — | below |

### 3.1 Segments

Segments butt-join on the timeline. Give each a `t0`/`t1`, or just `dur` (then `t0` = previous `t1`).

| key | meaning |
|---|---|
| `id` | unique name, used by transitions |
| `path` | video clip, or a still image (png/jpg) |
| `t0`, `t1` / `dur` | timeline in/out |
| `in` (alias `src`) | source seconds at `t0` (default 0) |
| `speed` | source seconds per timeline second (default 1; 2 = double speed; 0 = freeze) |
| `out` | source seconds at `t1`; sets `speed` to fit (if no `speed`/`ramp`) |
| `ramp` | piecewise time map `[[seg_t, src_t], ...]` with seg_t relative to `t0`. Linear between points and extrapolated past the ends, so handles keep moving. Example: `[[0,0],[0.75,0.75],[1.5,3.0]]` = 1× then 3× |
| `fit` | `"cover"` (default; fill and crop) or `"contain"` (letterbox on `bg`) |
| `anchor` | `[x, y]` crop position 0–1 for `cover` (default `[0.5, 0.5]`). Use it to reframe 16:9 sources for 9:16 |
| `frame` | reframing keys `[[seg_t, scale, dx, dy, angle_deg], ...]` (zoom about centre, shift px, rotate CCW); `frame_ease`: `linear` (default), `smooth`, `smoother`, `in`, `out`, `in_out` |
| `grade` | per-segment grade (§3.6) |
| `reveal` | a second plate shown through an expanding mask **within** the segment, persistent: `{"path", "in", "speed", "t0", "t1" (absolute), "cx", "cy", "r", "r0", "feather", "matte": "png", "ripple": px}`. For "part of the shot transforms" gags (before/after clips of the same framing) |
| `jolt` | damped vertical camera bump `[[t_abs, px], ...]` |
| `flip` | mirror horizontally |
| `blend` | `true` = blend adjacent frames (only for slow motion when no interpolated clip exists) |

### 3.2 Transitions

`{"a": "<seg id>", "b": "<seg id>", "kind": "...", "hw": 0.125, ...}`. `t` defaults to `b.t0`.
Active while `|time − t| ≤ hw`. Overlapping windows: the first one wins, and `--check` warns.

| kind | params (defaults) | what it does |
|---|---|---|
| `cut` | — | hard cut (no window) |
| `dissolve` | — | smoothstep cross-fade across the window (needs handles on both sides) |
| `whip` | `dir` left/right/up/down (left), `px` travel (0.78 W horizontal, 0.6 H vertical), `blur` px (460) | camera whip: A accelerates out with directional smear, B decelerates in. Edges reflect, so on vertical whips use less `px` and more `blur` to avoid a mirrored band (e.g. px 650, blur 560) |
| `bloom_in` | `amt` (0.9), `color` (look.bloom_color), `b_out` (0) | A blooms to a warm glow, then a hard cut into B; `b_out` > 0 decays a bloom on B |
| `bloom` | `amt` (1.0), `color` | symmetric: blooms up on A, back down on B |
| `dark_out` | `amt` (1.0), `color` (look.floor), `b_in` (0) | A sinks to near-black, then a hard cut out of the dark; `b_in` (0–1) lets B rise out of the dark |
| `dip` | `amt`, `color` (floor; `"#FFFFFF"` = flash) | symmetric dip through a colour |
| `roll_dark` | `total` deg (160), `dir` ccw/cw, `s0` (1.05), `sk` (0.9 zoom gain), `blur_deg` (30), `samples` (10), `cx`,`cy`, `b_in` (0.85), `b_dur` (0.25) | A barrel-rolls, accelerating with rotational blur and sinking into the floor colour; B rises out of the dark over `b_dur` s. Use a long window (hw 0.5–0.9) |
| `reveal` | `cx`,`cy` (centre), `r` (to farthest corner), `r0` (20), `feather` (120), `ease_p` (2), `matte` png, `ripple` px | B appears through an expanding feathered circle, optionally × a greyscale PNG matte (white = B), with optional water-ripple refraction on the edge. Always lands fully on B by the end of the window |
| `slide_still` | `element` png (RGBA), `width`, `scale`, `y` (H/2), `x0`/`x1` (enter right, exit left), `ease`, `cut_at` (0.5), `blur_k` (0.7), `max_blur` (400) | a still element crosses the frame with motion blur and hides the cut underneath. Make it big enough to cover the frame at `cut_at` |

### 3.3 Overlays

All overlays take `t0`, `t1` (absolute), and optionally `opacity`, `fade_in`, `fade_out` (seconds;
default hard on/off, which is how split captions read best). Text overlays take `style` (a name or an
inline dict) plus quick overrides `size`, `wght`, `color`, `track`.

| kind | keys |
|---|---|
| `title` | `text` (`\n` for lines), `x`, `y` (cap block centred on y; default frame centre), `part`: `{"at": seg-relative s, "dur": 0.5, "scale": 3, "travel": px, "blur": 60}` = exit with the first word leaving left and the rest right, scaling up with motion blur |
| `caption` | `left`, `right`; `layout`: `split` (left ink starts at `margin`, right ink ends at W − `margin`, same `baseline`; defaults margin = W/32 = 60 px at 1920, baseline = 0.551 H = 595 px) / `stack` (lines centred, block centred on `y`, default 0.62 H; `align`, `lines` [...]) / `auto` (default: stack when H > W) |
| `text` | `text` (`\n` ok), `x`, `y`, `align` (center/left/right by ink), `type_cps` (typed on at N characters/s; laid out at its final position; caret only while typing), `caret` (false to disable) |
| `image` | `path` (PNG with alpha), `x`, `y` (centre), `width` (px; else native), `scale`, `angle`, `blend` (`normal`/`screen`), `keys` `[[t_abs, x, y, scale, angle, opacity], ...]` + `keys_ease`; or `fit`: `cover`/`contain` for a full-frame plate |
| `video` | `path`, `in`, `speed`, `loop`; full frame (`fit`, `anchor`, `bg`, `blend: "screen"` for light/particle elements) or placed: `width`, `x`, `y`, `keys` |
| `card` | full-frame end card or slate: `bg` colour plate plus optional `path` (image or video; `fit` contain, `scale` = image width / W, `in`, `speed`). While fully opaque the picture underneath is not rendered. A 30 fps end-card video is conformed to 24 by nearest frame (frames dropped, not blended). Not affected by `finish` |
| `ui` | prompt pill and cursor (§3.4): `moves` `[[t_abs, x, y], ...]` + `moves_ease`, `clicks` `[t_abs, ...]`, `pills` `[{"t0","t1","text","cps", "dx","dy" (offset from cursor; default 34, −46) or "x","y" (left-centre)}]`, `cursor`/`pill` overrides, `fade` (0.12), `hide_cursor` |
| `slide_still` | `path` (RGBA), `width`, `keys` `[[t_abs, cx, cy, scale], ...]`, `blur_k` (0.7 × px/frame), `max_blur`, `grade` |

### 3.4 UI devices (generic)

- `ui.cursor`: `image` (a PNG; `null` draws a ring), `size` (px width, 40), `hotspot` ([0.5,0.5] of the
  sprite; this point sits on the path and is the spin centre; an arrow's tip is about [0.06,0.06]),
  `color` (ring/pulse), `spin_deg` (360 per click, CCW, curve 360·(1−(1−u)³); 0 = none), `click_dur`
  (0.34), `pulse` (true), `shadow` (0.35).
- `ui.pill`: `style` (`"pill"`), `height` (1.6 × size), `pad_x`, `radius` (height/2), `fill` (#000) +
  `fill_opacity` (0.32), `outline` (#FFF) + `outline_opacity` (0.92) + `outline_width` (2), `grow`
  (false: the pill is sized for the full text and the text types into it).
- Type the pill fast (60–110 cps) so it finishes and holds. The caret disappears when typing ends;
  a trailing caret was read as a letter.

### 3.5 Typography

- **Always set the weight.** A variable font opens at its default instance, often its lightest.
  Every style applies `wght` (font spec, style or overlay; default 600) via the font's axes. Other
  axes go in `"axes": {"opsz": 32, "wdth": 90}`. A static font ignores `wght` (with a warning), so
  point at the static file of the weight you want.
- Alignment is by **ink**, measured on rasterised glyphs. Left captions start exactly at the
  margin, right captions end exactly at W − margin, and titles centre their cap block on y.
- Glyphs are rendered at 2× and downsampled with Lanczos. Tracking (`track`, em) is applied per
  character while keeping kerning (ligatures are not formed when `track` ≠ 0).
- The soft shadow is `{"amount": 0–1, "sigma": px, "dx", "dy"}`: a multiply darkening under the
  ink, for legibility on busy plates. It is not an outline or a box.
- Defaults (px, from min(W, H) = 1080): title 162, caption 67, text 113, pill 38; all wght 600.
  Calibrate size/weight against the reference's ink (cap height, stem width, ink extents).
  Reference production, measured: title 199 px, captions 67 px on baseline 595 with 60 px
  margins, tagline 136 px typed at 34 cps, lockup 104 px, pill 38 px.

### 3.6 Grade and finish

`grade` keys, applied in this order (all optional): `exposure` (stops), `temp` (+warm/−cool, ~±0.4
typical), `tint` (+magenta/−green), `contrast` (pivot 0.45), `sat` (−0.2…+0.1 typical), `lift`
(scalar or `[r,g,b]`: raises blacks, out + lift·(1−out)), `gamma` (scalar or rgb), `gain` (scalar or
rgb), `milky` (0–0.1: blend toward `look.floor` for milky, never-black shadows), `rgb` (multipliers),
`light` (`{"dir": "left|right|top|bottom|flat", "color": [...], "amount": 0.25}`: a screen-blended
coloured light ramp, for "relight" gags).

`finish`: `grade` (whole picture), `halation` (0.05; `halation_threshold` 0.74, `halation_sigma`
16, `halation_tint` [1, 0.82, 0.62]), `vignette` (0–0.3), `grain` (0.016, strongest in mid-tones,
deterministic per frame; `grain_seed`).

---

## 4. Complete minimal example

Three segments, a whip and a dark_out, a title that parts, split captions, a typed tagline and an
end card (rendered and verified as written). The project layout:

```
gens/S01_open/out_0.mp4  gens/S02_reveal/out_0.mp4  gens/S03_hero/out_0.mp4
brand/fonts/Brand-VF.ttf  brand/endcard.png  music/score_v3.mp3  sfx/<name>/out_0.wav
comp/cutlist.json  comp/mix.json  comp/out/  deliverables/
```

`comp/cutlist.json`:

```json
{
  "version": 1,
  "root": "..",
  "width": 1920, "height": 1080, "fps": 24, "duration": 12.0,
  "look": {"floor": "#1E1F1C", "bloom_color": "#F3ECDF"},
  "fonts": {"main": {"path": "brand/fonts/Brand-VF.ttf", "wght": 600}},
  "styles": {
    "title":   {"size": 180, "track": -0.02, "shadow": {"amount": 0.18, "sigma": 14}},
    "caption": {"size": 67,  "track": -0.025, "shadow": {"amount": 0.25, "sigma": 9}},
    "text":    {"size": 120, "track": -0.02, "shadow": {"amount": 0.22, "sigma": 12}}
  },
  "finish": {"halation": 0.05, "grain": 0.016},
  "segments": [
    {"id": "open",   "path": "gens/S01_open/out_0.mp4",   "t0": 0.0, "t1": 4.0, "in": 0.5,
     "frame": [[0, 1.0, 0, 0, 0], [4.0, 1.05, 0, 0, 0]]},
    {"id": "reveal", "path": "gens/S02_reveal/out_0.mp4", "t0": 4.0, "t1": 7.5,
     "ramp": [[0, 0.0], [1.5, 1.5], [3.5, 4.5]], "grade": {"temp": 0.2, "sat": -0.05}},
    {"id": "hero",   "path": "gens/S03_hero/out_0.mp4",   "t0": 7.5, "t1": 10.5, "in": 0.5}
  ],
  "transitions": [
    {"a": "open",   "b": "reveal", "kind": "whip", "dir": "left", "hw": 0.125, "px": 900, "blur": 300},
    {"a": "reveal", "b": "hero",   "kind": "dark_out", "hw": 0.2, "b_in": 0.35}
  ],
  "overlays": [
    {"kind": "title",   "t0": 0.0, "t1": 2.5, "text": "Your Title", "part": {"at": 2.0, "dur": 0.5}},
    {"kind": "caption", "t0": 4.5, "t1": 7.0, "left": "Every wish", "right": "is a prompt"},
    {"kind": "text",    "t0": 8.0, "t1": 10.3, "text": "Anything can happen", "type_cps": 24, "fade_out": 0.25},
    {"kind": "card",    "t0": 10.5, "t1": 12.0, "path": "brand/endcard.png", "bg": "#101010", "scale": 0.5, "fade_in": 0.2}
  ]
}
```

Reading it: `open` plays source 0.5–4.5 s with a 5 % push-in. `reveal` plays 1× for 1.5 s, then
1.5× (source 1.5→4.5 over 2 s). The end card covers 10.5–12.0 s with a 0.2 s fade from the last
frame of `hero`. Other overlay types, as they would sit in `overlays`:

```json
{"kind": "ui", "t0": 18.15, "t1": 19.4, "moves": [[18.15, 1990, 700], [18.7, 905, 335], [19.3, 1000, 520]],
 "moves_ease": "smooth", "clicks": [19.0], "pills": [{"t0": 18.3, "t1": 19.12, "text": "bring it to life", "cps": 100}]},
{"kind": "image", "t0": 5.2, "t1": 6.4, "path": "brand/badge.png", "width": 300, "fade_in": 0.2,
 "keys": [[5.2, 1500, 300, 1.0, 0], [6.4, 1650, 330, 1.1, 20]]},
{"kind": "slide_still", "t0": 29.2, "t1": 29.95, "path": "comp/assets/island.png", "keys": [[29.2, 3000, 300, 1.3], [29.95, -900, 300, 1.3]], "blur_k": 0.7}
```

and `"ui": {"cursor": {"image": "brand/cursor.png", "size": 40, "hotspot": [0.5, 0.5]}}` at top level.

---

## 5. Mix schema

| key | default | meaning |
|---|---|---|
| `duration` | — | **required**; equals the cut's duration (the mix is padded/trimmed to it exactly) |
| `root` | `"."` | base folder for relative paths |
| `sample_rate` | 48000 | |
| `music` | — | one track or a list: `file`, `at` (timeline start), `trim_start` (source seconds cut from the head; use it to put beats on your grid, see `music_analyze.py` `advance_s`), `gain_db`, `loop`, `duration`, `fade_in` (s or `{duration, curve}`), `fade_out` `{start (timeline s), duration, curve}` (ffmpeg afade curves: `tri`, `qsin`, `exp`, `log`, ...) |
| `sfx` | [] | cues: `start` (timeline s), `file`, `gain_db`, `duration`, `fade_in`, `fade_out` (seconds at the end of the cue, or `{start, duration, curve}`), `loop` (with `-stream_loop`; runs to `duration` or the end), `trim_start` |
| `sfx_normalize` | `{"peak_db": -3, "max_boost_db": 24}` | each SFX file is peak-normalised first (generated SFX levels vary by up to ~48 dB), so `gain_db` means the same thing for every cue; `false` disables |
| `sfx_bus_db` | 0 | added to every SFX (the reference mix used +9 with cue gains around −10 to −27) |
| `master` | `{"I": -14, "TP": -1.5, "LRA": 11, "two_pass": true, "codec": "pcm_s24le"}` | loudness target; pass 2 uses the measured values with `linear=true`. The script prints `normalization_type`; `dynamic` means the true-peak ceiling forced compression |

The summed premix is 32-bit float (`amix normalize=0`), so nothing clips before the master stage.

`comp/mix.json` for the example above:

```json
{
  "root": "..",
  "duration": 12.0,
  "sample_rate": 48000,
  "music": {"file": "music/score_v3.mp3", "trim_start": 0.1, "at": 0.0, "gain_db": -1,
            "fade_out": {"start": 9.0, "duration": 3.0, "curve": "tri"}},
  "sfx_bus_db": 0,
  "sfx_normalize": {"peak_db": -3, "max_boost_db": 24},
  "sfx": [
    {"start": 0.0,  "file": "sfx/room_tone/out_0.wav", "gain_db": -26, "duration": 7.5, "loop": true, "fade_out": 0.5},
    {"start": 3.85, "file": "sfx/whoosh/out_0.wav",    "gain_db": -12},
    {"start": 7.5,  "file": "sfx/impact/out_0.wav",    "gain_db": -10},
    {"start": 8.0,  "file": "sfx/key_tap/out_0.wav",   "gain_db": -20, "duration": 0.2, "fade_out": 0.05}
  ],
  "master": {"I": -14, "TP": -1.5, "LRA": 11, "two_pass": true, "codec": "pcm_s24le"}
}
```

Place whooshes about 0.1–0.15 s **before** the whip's centre, and impacts on the cut frame. A room
tone or bed that stops dead on a cut sells the cut. `mix.py measure` prints both the ebur128 and the
loudnorm meters, which can differ by a few tenths of a LU on short or synthetic material. The
reference master read −13.9 LUFS, −2.7 dBFS peak, LRA 5.3 LU.

---

## 6. Making cutdowns

A 30 s, 15 s or 9:16 version is **another cut list over the same source clips**. It is never cut
from the finished master, which has type and grain burned in.

- Copy `cutlist.json` to `cutlist_30s.json`. Keep the beats tagged for the cutdown, delete the rest,
  and retime with `t0/t1` (or `dur`), `in`, `speed`/`ramp`. Run `--check` and fix the warnings.
- Vertical: set `"width": 1080, "height": 1920`. Reframe every segment with `anchor` (the crop slides
  along the 16:9 source; `[0.3, 0.5]` keeps the left third) and/or `frame` keys. Captions with
  `layout: "auto"` stack automatically. Re-check type sizes against the 1080 px width. Keep text
  inside the platform-safe area (roughly the top 14 % and bottom 20 % carry platform UI).
- Shorter cuts usually need their own music edit or take (`mix_30s.json`: a different `trim_start`,
  `fade_out`, or a 30 s take generated with its own section map) and their own SFX list.
- Same commands, different files:
  `render.py comp/cutlist_9x16.json --out comp/out/cut_9x16_v1.mp4`, then mix and mux with
  `mix_9x16.json`.

---

## 7. Optional music tools (librosa)

```bash
python $S/music_analyze.py music/take_*.mp3 --grid 0.5 --hits 6,12,18,26 \
    --sections "0-6:2,6-12:4,12-18:8,18-26:6" --json music/analysis.json
python $S/cover_detect.py --ref refs/reference_audio.wav music/take_a.mp3 music/take_b.mp3 \
    --neg music/known_unrelated_1.mp3 music/known_unrelated_2.mp3
```

- `music_analyze.py`: `grid_coh` (1.0 = beats exactly on your cut grid); `advance_s` (trim this off
  the head with `trim_start` so beats land on grid lines); `hits` (accent strength at each cut you need
  supported); `corr_energy` (does the take follow your energy curve?); and the beat times.
- `cover_detect.py`: key-invariant, tempo-tolerant Qmax similarity. **It has no fixed threshold:**
  calibrate every time. Positive controls (the reference pitch-shifted and re-tempo'd, and an
  excerpt) are built automatically. Supply negative controls of known-unrelated music in the same
  genre. A take is flagged when it scores closer to the positives. In the test, a +3-semitone,
  110→120 BPM copy scored Qmax 65.6 against negatives at 4.8–5.7 and an original take at 4.2.

## 8. Gotchas

- `--check` before every render: most "bugs" are a segment running off the end of its clip (a frozen
  frame) or a dissolve without handles.
- Untagged sources: the renderer assumes BT.709 for HD. If colours shift against the source, set
  `"source_matrix": "bt601"` (or re-tag the clips). Output is tagged BT.709.
- Big `px` on vertical whips shows the reflected edge; reduce `px`, raise `blur`.
- Review stills are PNG. The renderer has no audio; mux every review cut, because sound notes come
  in most rounds.
