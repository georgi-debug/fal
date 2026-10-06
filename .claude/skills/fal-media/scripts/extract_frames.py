#!/usr/bin/env python3
"""Extract the first frame, the last frame, or the frame at time t from a video.

Chaining clips: the last frame of clip N becomes the first-frame keyframe (image_url) of
clip N+1, so the cut is seamless. Save as PNG to avoid a generation of JPEG loss, then
upload it (MCP upload_file) and pass the URL. Also handy for full-resolution QA of faces,
hands and small props at a given time.

Usage
    python3 extract_frames.py VIDEO OUT.png --last          # for chaining into the next clip
    python3 extract_frames.py VIDEO OUT.png --first
    python3 extract_frames.py VIDEO OUT.png --at 2.75       # seconds from the start

Prints the output path, its size and the source duration.
Requires ffmpeg + ffprobe on PATH.
Exit codes: 0 ok, 1 usage error / missing input / t outside the video, 3 ffmpeg failure.
"""
import argparse
import json
import os
import subprocess
import sys


def duration(video):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                        video], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr.strip()[:300]}")
    return float(json.loads(p.stdout)["format"]["duration"])


def image_size(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True)
    return p.stdout.strip()


def extract(video, out, mode, t=None):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    q = ["-q:v", "2"] if os.path.splitext(out)[1].lower() in (".jpg", ".jpeg") else []
    if mode == "first":
        cmd = base + ["-i", video, "-map", "0:v:0", "-frames:v", "1"] + q + [out]
    elif mode == "at":
        cmd = base + ["-ss", f"{t:.4f}", "-i", video, "-map", "0:v:0", "-frames:v", "1"] + q + [out]
    else:
        # Decode the last few seconds and keep overwriting one image: the file left behind is
        # the final decoded frame, exact regardless of keyframe placement.
        cmd = base + ["-sseof", "-3", "-i", video, "-map", "0:v:0", "-update", "1"] + q + [out]
    if os.path.exists(out):
        os.remove(out)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.strip()[:400] or 'no frame written'}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="first / last / at-time frame of a video",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 ok, 1 usage/bad input, 3 ffmpeg failure.")
    ap.add_argument("video")
    ap.add_argument("out", help="output image (.png recommended)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--first", action="store_true")
    g.add_argument("--last", action="store_true")
    g.add_argument("--at", type=float, metavar="SECONDS")
    a = ap.parse_args(argv)
    if not os.path.exists(a.video):
        print(f"extract_frames: no such file: {a.video}", file=sys.stderr)
        return 1
    try:
        dur = duration(a.video)
        if a.at is not None and not (0 <= a.at < dur):
            print(f"extract_frames: --at {a.at} is outside the video (0 to {dur:.3f}s)",
                  file=sys.stderr)
            return 1
        mode = "first" if a.first else "last" if a.last else "at"
        extract(a.video, a.out, mode, a.at)
    except RuntimeError as e:
        print(f"extract_frames: {e}", file=sys.stderr)
        return 3
    print(f"{a.out}  {image_size(a.out).replace(',', 'x')}  ({mode}{'' if a.at is None else f' {a.at}s'}"
          f" of {dur:.3f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
