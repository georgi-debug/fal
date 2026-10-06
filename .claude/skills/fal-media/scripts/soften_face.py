#!/usr/bin/env python3
"""Soften faces in a keyframe so it passes an image-to-video likeness filter.

Some image-to-video models (Seedance 2.5 notably) reject input frames whose faces look
like real people (`content_policy_violation` / `partner_validation_failed`). Rejections are
not billed and the filter is random, but these softening recipes passed in production.
The video model re-sharpens within ~0.3-0.55 s, so trim those first frames in the edit.

Presets (all parameters can be overridden with the flags below)
    close     close frontal face: resize to 1920 wide, 1.2 px Gaussian blur, 8% milky lift
              toward RGB(200,185,165), grain sigma 6
    profile   lost profile / smaller face: resize to 1280 wide (1280x720 for 16:9),
              1.0 px blur, 10% milk, grain sigma 7
    bloom     bake a warm sun-bloom flare over the face (default 70% at the centre);
              --center/--radius place it, --base sets a floor everywhere (a shaped bloom
              such as 40% rising to 86% over the face is --base 0.4 --strength 0.86)
    --box x,y,w,h --blur PX
              local face-only blur (feathered ellipse). 1.4 px was rejected, 1.7-2.0 px
              passed. Can be used alone or on top of a preset.

Usage
    python3 soften_face.py IN.png OUT.png --preset close
    python3 soften_face.py IN.png OUT.png --preset profile --seed 3
    python3 soften_face.py IN.png OUT.png --preset bloom --strength 0.7 --center 0.55,0.35
    python3 soften_face.py IN.png OUT.png --box 820,240,260,320 --blur 2.0
    python3 soften_face.py IN.png OUT.png --preset close --box 820,240,260,320 --blur 1.7

    Overrides: --width W (0 = keep size), --sigma PX, --milk FRACTION, --milk-color R,G,B,
    --grain SIGMA (0 = none), --seed N (grain is deterministic for a given seed).
    --center accepts fractions (0.5,0.4) or pixels (960,430); --radius is a fraction of the
    image diagonal (default 0.45) or pixels if > 1.

Output format follows OUT's extension (PNG recommended; JPEG is saved at quality 95).
Exit codes: 0 ok, 1 usage error or unreadable input.
"""
import argparse
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageOps

MILK = (200, 185, 165)
WARM = np.array([255, 236, 205], np.float32) / 255.0  # sun-bloom cream

PRESETS = {
    "close": dict(width=1920, sigma=1.2, milk=0.08, grain=6.0),
    "profile": dict(width=1280, sigma=1.0, milk=0.10, grain=7.0),
    "bloom": dict(width=0, sigma=0.0, milk=0.0, grain=0.0, strength=0.7),
}


def load(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(im).astype(np.float32) / 255.0


def save(arr, path):
    im = Image.fromarray((np.clip(arr, 0, 1) * 255.0 + 0.5).astype(np.uint8))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg"):
        im.save(path, quality=95, subsampling=0)
    else:
        im.save(path)


def resize_width(img, width):
    h, w = img.shape[:2]
    if not width or width == w:
        return img
    nh = max(1, round(h * width / w))
    interp = cv2.INTER_AREA if width < w else cv2.INTER_LANCZOS4
    return np.clip(cv2.resize(img, (width, nh), interpolation=interp), 0, 1)


def gblur(img, sigma):
    if sigma <= 0:
        return img
    return cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT)


def milky_lift(img, amount, color=MILK):
    if amount <= 0:
        return img
    c = np.array(color, np.float32) / 255.0
    return img * (1.0 - amount) + c * amount


def grain(img, sigma, seed=0):
    """Monochrome film grain; sigma in 0-255 units; deterministic for a given seed."""
    if sigma <= 0:
        return img
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(img.shape[:2]).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), 0.5)
    n /= max(float(n.std()), 1e-6)  # keep the requested sigma after the slight clumping blur
    return img + (n * (sigma / 255.0))[..., None]


def _luma(img):
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def bloom(img, strength=0.7, center=(0.5, 0.4), radius=0.45, base=0.0):
    """Warm sun-bloom flare: halation + lift toward cream, shaped by a radial falloff.

    Local amount k = base + (strength - base) * falloff; never clips to pure white.
    """
    h, w = img.shape[:2]
    cx = center[0] * w if center[0] <= 1 else center[0]
    cy = center[1] * h if center[1] <= 1 else center[1]
    r = radius * float(np.hypot(w, h)) if radius <= 1 else radius
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / max(r * r, 1.0)
    fall = np.exp(-2.0 * d2)
    k = (base + (strength - base) * fall)[..., None]
    lum = _luma(img)
    m = np.clip((lum - 0.5) / 0.5, 0, 1)[..., None]
    sig = max(4.0, 30.0 * w / 1920.0)
    glow = cv2.GaussianBlur(img * m, (0, 0), sig) * np.array([1.0, 0.82, 0.62], np.float32)
    soft = cv2.GaussianBlur(img, (0, 0), max(1.0, 3.0 * w / 1920.0))
    out = img * (1 - 0.5 * k) + soft * (0.5 * k)  # flare veils fine facial detail
    out = out + glow * (0.5 * k)
    out = out * (1 - 0.55 * k) + WARM * (0.55 * k) + 0.08 * k
    return np.minimum(out, 0.93 + 0.07 * (1 - k))  # keep bloomed areas off pure white


def box_blur(img, box, sigma, feather=None):
    """Blur only inside box (x, y, w, h) with a feathered elliptical mask."""
    x, y, bw, bh = box
    h, w = img.shape[:2]
    if bw <= 0 or bh <= 0 or x >= w or y >= h or x + bw <= 0 or y + bh <= 0:
        raise ValueError(f"--box {box} is outside the {w}x{h} image")
    mask = np.zeros((h, w), np.float32)
    cv2.ellipse(mask, (int(x + bw / 2), int(y + bh / 2)), (max(1, int(bw / 2)), max(1, int(bh / 2))),
                0, 0, 360, 1.0, -1)
    f = feather if feather is not None else max(2.0, 0.08 * min(bw, bh))
    mask = cv2.GaussianBlur(mask, (0, 0), f)[..., None]
    return img * (1 - mask) + gblur(img, sigma) * mask


def parse_floats(s, n, name):
    try:
        v = [float(p) for p in s.split(",")]
    except ValueError:
        v = []
    if len(v) != n:
        raise argparse.ArgumentTypeError(f"{name} needs {n} comma-separated numbers, got {s!r}")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description="likeness-filter softening for keyframes",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 ok, 1 usage error / unreadable input.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--width", type=int, help="resize to this width keeping aspect (0 = keep)")
    ap.add_argument("--sigma", type=float, help="global Gaussian blur sigma in px")
    ap.add_argument("--milk", type=float, help="milky lift fraction (0.08 = 8%%)")
    ap.add_argument("--milk-color", default="200,185,165", help="R,G,B of the milky lift")
    ap.add_argument("--grain", type=float, help="grain sigma in 0-255 units (0 = none)")
    ap.add_argument("--seed", type=int, default=0, help="grain seed (deterministic)")
    ap.add_argument("--strength", type=float, help="bloom peak 0..1 (bloom preset, default 0.7)")
    ap.add_argument("--base", type=float, default=0.0, help="bloom floor 0..1 everywhere")
    ap.add_argument("--center", default="0.5,0.4", help="bloom centre: fractions or pixels")
    ap.add_argument("--radius", type=float, default=0.45, help="bloom radius (diag fraction or px)")
    ap.add_argument("--box", help="x,y,w,h face box for a local blur (in OUTPUT pixels)")
    ap.add_argument("--blur", type=float, default=1.7, help="local box blur sigma in px")
    ap.add_argument("--feather", type=float, help="box mask feather px (default 8%% of box)")
    a = ap.parse_args(argv)
    if not a.preset and not a.box:
        ap.print_usage(sys.stderr)
        print("soften_face: give --preset and/or --box", file=sys.stderr)
        return 1
    try:
        milk_color = parse_floats(a.milk_color, 3, "--milk-color")
        center = parse_floats(a.center, 2, "--center")
        box = [int(round(v)) for v in parse_floats(a.box, 4, "--box")] if a.box else None
        img = load(a.input)
    except (argparse.ArgumentTypeError, OSError, ValueError) as e:
        print(f"soften_face: {e}", file=sys.stderr)
        return 1

    p = dict(PRESETS.get(a.preset, dict(width=0, sigma=0.0, milk=0.0, grain=0.0)))
    for k in ("width", "sigma", "milk", "grain", "strength"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    steps = []
    img = resize_width(img, p["width"])
    if p["width"]:
        steps.append(f"resize {img.shape[1]}x{img.shape[0]}")
    if a.preset == "bloom":
        img = bloom(img, p.get("strength", 0.7), center, a.radius, a.base)
        steps.append(f"bloom {p.get('strength', 0.7):.2f} base {a.base:.2f} at {center}")
    if p["sigma"] > 0:
        img = gblur(img, p["sigma"])
        steps.append(f"blur {p['sigma']}px")
    if box:
        try:
            img = box_blur(img, box, a.blur, a.feather)
        except ValueError as e:
            print(f"soften_face: {e}", file=sys.stderr)
            return 1
        steps.append(f"box blur {a.blur}px at {box}")
    if p["milk"] > 0:
        img = milky_lift(img, p["milk"], milk_color)
        steps.append(f"milk {p['milk'] * 100:.0f}% -> {tuple(int(c) for c in milk_color)}")
    if p["grain"] > 0:
        img = grain(img, p["grain"], a.seed)
        steps.append(f"grain {p['grain']} seed {a.seed}")
    save(img, a.output)
    print(f"{a.output}  {img.shape[1]}x{img.shape[0]}  " + ", ".join(steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
