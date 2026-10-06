#!/usr/bin/env python3
"""Timestamped contact sheet of a video, for frame-by-frame review of generated clips.

Never judge a clip from one frame: lay out frames across its whole length, each labelled
with its timecode (and optionally the edit segment it belongs to), then look for
mutating faces/hands, debris, motion direction and where the clip goes wrong.

Usage
    python3 contact_sheet.py VIDEO OUT.jpg [--fps 4 | --every K] [--cols 6] [--width 480]
                             [--start S] [--end S] [--segments segs.json] [--max-frames 240]

    contact_sheet.py clip.mp4 sheet.jpg                      # 4 frames/s, 6 columns
    contact_sheet.py clip.mp4 sheet.png --every 0.5 --cols 8 --width 320
    contact_sheet.py cut.mp4 sheet.jpg --start 10 --end 14 --fps 8
    contact_sheet.py cut.mp4 sheet.jpg --every 1 --segments segments.json

    segments.json: a list of {"t0": 0.0, "t1": 5.5, "label": "A1"} (also accepts
    "start"/"end" and "id"/"name"), in the video's own timeline. Each frame gets the label
    of the segment containing it and a coloured strip; the strip colour changes at cuts.

    Output format follows the extension (.jpg/.jpeg/.png/.webp). Prints the output path and
    the video's duration / size / frame rate.

Requires ffmpeg + ffprobe on PATH and Pillow.

Exit codes: 0 ok, 1 usage error / bad input, 3 ffmpeg/ffprobe failure or no frames.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

STRIP_COLORS = [(255, 196, 0), (0, 190, 255), (255, 90, 120), (120, 220, 90), (190, 130, 255),
                (255, 150, 60)]


def probe(video):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,r_frame_rate,start_time:format=duration,start_time", "-of", "json", video]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr.strip()[:400]}")
    d = json.loads(p.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    if not st:
        raise RuntimeError("no video stream")
    num, _, den = str(st.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    fmt = d.get("format", {})
    t_base = st.get("start_time", fmt.get("start_time"))
    try:
        t_base = float(t_base)
    except (TypeError, ValueError):
        t_base = 0.0
    return {"duration": float(fmt.get("duration") or 0), "width": st.get("width"),
            "start_time": t_base,
            "height": st.get("height"), "fps": fps}


def fmt_tc(t):
    if t < 60:
        return f"{t:.2f}s"
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}"


def load_segments(path):
    with open(path) as f:
        segs = json.load(f)
    if isinstance(segs, dict):
        segs = segs.get("segments", [])
    out = []
    for s in segs:
        t0 = s.get("t0", s.get("start"))
        t1 = s.get("t1", s.get("end"))
        label = s.get("label", s.get("id", s.get("name", "")))
        if t0 is None or t1 is None:
            raise ValueError(f"segment needs t0/t1 (or start/end): {s}")
        out.append((float(t0), float(t1), str(label)))
    return sorted(out)


def segment_at(segs, t):
    for i, (t0, t1, label) in enumerate(segs):
        if t0 <= t < t1:
            return i, label
    return None, ""


def get_font(size):
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def build(video, out, fps=None, every=None, cols=6, width=480, start=0.0, end=None,
          segments=None, max_frames=240, quality=88):
    info = probe(video)
    dur = info["duration"]
    end = dur if end is None or end > dur else end
    start = max(0.0, start or 0.0)
    if end <= start:
        raise ValueError(f"--end ({end}) must be after --start ({start}); duration is {dur:.3f}s")
    rate = (1.0 / every) if every else (fps or 4.0)
    n_expected = int((end - start) * rate + 1e-6) + 1
    if n_expected > max_frames:
        raise ValueError(f"{n_expected} frames > --max-frames {max_frames}; lower --fps, raise "
                         f"--every, narrow --start/--end, or raise --max-frames")
    step = 1.0 / rate
    t_base = info["start_time"]
    eps = 0.5 / info["fps"] if info["fps"] > 0 else 0.02  # half a source frame -> nearest frame
    t0_abs, t1_abs = t_base + start, t_base + end
    # Pick the first frame at/after each target time start + k*step (exact source frames,
    # no fps-filter rounding), and log each picked frame's real timestamp with showinfo.
    sel = (f"select='gte(t\\,{t0_abs:.6f}+selected_n*{step:.6f}-{eps:.6f})"
           f"*lte(t\\,{t1_abs + eps:.6f})',showinfo,scale={width}:-2")
    with tempfile.TemporaryDirectory(prefix="contact_") as d:
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
               "-ss", f"{max(0.0, start - 1.0):.4f}", "-t", f"{end - max(0.0, start - 1.0) + 1.0:.4f}",
               "-copyts", "-i", video, "-map", "0:v:0", "-vf", sel, "-vsync", "0",
               "-q:v", "2", os.path.join(d, "f_%05d.jpg")]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {p.stderr.strip()[-400:]}")
        times = [float(m) - t_base for m in
                 re.findall(r"Parsed_showinfo.*?pts_time:\s*([-0-9.eE+]+)", p.stderr)]
        files = sorted(glob.glob(os.path.join(d, "f_*.jpg")))[:max_frames]
        if not files:
            raise RuntimeError("ffmpeg produced no frames")
        if len(times) != len(files):  # fall back to nominal times
            times = [start + i * step for i in range(len(files))]
        frames = [Image.open(f).convert("RGB") for f in files]
    W, H = frames[0].size
    fsize = max(12, W // 22)
    font = get_font(fsize)
    bar = fsize + 8
    strip = 4 if segments else 0
    cols = max(1, min(cols, len(frames)))
    rows = (len(frames) + cols - 1) // cols
    head = bar + 4
    sheet = Image.new("RGB", (cols * W, head + rows * (H + bar + strip)), (0, 0, 0))
    dr = ImageDraw.Draw(sheet)
    title = (f"{os.path.basename(video)}  {info['width']}x{info['height']}  {info['fps']:.3g} fps  "
             f"{dur:.2f}s  |  {start:.2f}-{end:.2f}s every {1 / rate:.3g}s  ({len(frames)} frames)")
    dr.text((4, 3), title, fill=(220, 220, 220), font=font)
    for i, im in enumerate(frames):
        t = times[i]
        x, y = (i % cols) * W, head + (i // cols) * (H + bar + strip)
        label = fmt_tc(t)
        if segments:
            si, seg = segment_at(segments, t)
            if seg:
                label += f"  {seg}"
            col = STRIP_COLORS[si % len(STRIP_COLORS)] if si is not None else (60, 60, 60)
            dr.rectangle([x, y + bar, x + W - 1, y + bar + strip - 1], fill=col)
        dr.text((x + 4, y + 4), label, fill=(255, 235, 0), font=font)
        sheet.paste(im, (x, y + bar + strip))
        if i % cols:
            dr.line([x, y, x, y + bar + strip + H], fill=(0, 0, 0), width=2)
    ext = os.path.splitext(out)[1].lower()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if ext in (".jpg", ".jpeg", ".webp"):
        sheet.save(out, quality=quality)
    else:
        sheet.save(out)
    return info, len(frames), sheet.size


def main(argv=None):
    ap = argparse.ArgumentParser(description="timestamped contact sheet of a video",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 ok, 1 usage/bad input, 3 ffmpeg failure.")
    ap.add_argument("video")
    ap.add_argument("out", help="output image (.jpg/.png/.webp)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fps", type=float, help="frames per second to sample (default 4)")
    g.add_argument("--every", type=float, help="sample one frame every K seconds")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--width", type=int, default=480, help="thumbnail width in px")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--segments", help="JSON list of {t0, t1, label}")
    ap.add_argument("--max-frames", type=int, default=240)
    ap.add_argument("--quality", type=int, default=88, help="JPEG/WebP quality")
    a = ap.parse_args(argv)
    if not os.path.exists(a.video):
        print(f"contact_sheet: no such file: {a.video}", file=sys.stderr)
        return 1
    if (a.fps is not None and a.fps <= 0) or (a.every is not None and a.every <= 0) or a.width < 16:
        print("contact_sheet: --fps/--every must be > 0 and --width >= 16", file=sys.stderr)
        return 1
    try:
        segs = load_segments(a.segments) if a.segments else None
        info, n, size = build(a.video, a.out, a.fps, a.every, a.cols, a.width, a.start, a.end,
                              segs, a.max_frames, a.quality)
    except (ValueError, OSError) as e:
        print(f"contact_sheet: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"contact_sheet: {e}", file=sys.stderr)
        return 3
    print(f"{a.out}  {n} frames  {size[0]}x{size[1]}  | source {info['width']}x{info['height']} "
          f"{info['fps']:.3g} fps {info['duration']:.3f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
