#!/usr/bin/env python3
"""True slow motion by motion-compensated interpolation (pre-render a source clip, then cut it in normally).

  python slowmo.py gens/E1/out_0.mp4 comp/slow/E1_089.mp4 --factor 0.89

Runs exactly the filter used in production for 0.89x:
  setpts=PTS/0.89,minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1
The output is 1/factor times longer; in the cut list, source time in the slowed file = original time / factor.
Prefer this over frame blending (ghosting) for anything below ~0.95x. Check the result for warping on fast or
occluding motion (mci invents pixels); keep factors mild (0.8-0.95) unless the motion is simple.
"""
import argparse
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--factor", type=float, required=True, help="speed factor < 1, e.g. 0.89")
    ap.add_argument("--fps", type=float, default=24)
    ap.add_argument("--crf", type=int, default=14)
    a = ap.parse_args()
    if not 0 < a.factor <= 1:
        sys.exit("--factor must be in (0, 1]")
    vf = (f"setpts=PTS/{a.factor:g},minterpolate=fps={a.fps:g}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", a.src, "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "slow", "-crf", str(a.crf), "-pix_fmt", "yuv420p", a.out]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
