"""Frame engine for the cut-list renderer: decoding, easing, image ops, grading, transitions, encoding.

Brand-neutral. Every frame is float32 RGB in 0..1, shape (H, W, 3). RGBA layers are (h, w, 4).
Nothing here knows about a particular spot; colours, sizes and paths come from the cut list.

Used by render.py (the cut-list renderer), typo.py and ui.py. Requires numpy, opencv-python(-headless),
pillow and ffmpeg/ffprobe on PATH.
"""
import json
import math
import os
import subprocess
from collections import OrderedDict

import cv2
import numpy as np

cv2.setNumThreads(max(1, (os.cpu_count() or 2)))

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


# ------------------------------------------------------------------ colours
def color(c, default=(1.0, 1.0, 1.0)):
    """'#RRGGBB' | [r,g,b] (0..255 if any value > 1, else 0..1) | None -> float32 array (3,) in 0..1."""
    if c is None:
        return np.array(default, np.float32)
    if isinstance(c, str):
        s = c.lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0
    a = np.array(c[:3], np.float32)
    return a / 255.0 if a.max() > 1.0 else a


# ------------------------------------------------------------------ probing
_PROBE = {}


def probe(path):
    """width, height, fps, duration, color_space of the first video stream (cached)."""
    if path in _PROBE:
        return _PROBE[path]
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,color_space,duration:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr.strip()}")
    j = json.loads(r.stdout)
    st = (j.get("streams") or [{}])[0]

    def rate(s):
        try:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        except Exception:
            return 0.0

    fps = rate(st.get("avg_frame_rate", "0/1")) or rate(st.get("r_frame_rate", "0/1")) or 24.0
    dur = float(st.get("duration") or j.get("format", {}).get("duration") or 0.0)
    info = dict(width=int(st.get("width", 0)), height=int(st.get("height", 0)), fps=fps, duration=dur,
                color_space=st.get("color_space", "unknown"))
    _PROBE[path] = info
    return info


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXT


# ------------------------------------------------------------------ stills
def load_rgba(path):
    """Any image -> float32 RGBA at native size (alpha = 1 when the file has none)."""
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise FileNotFoundError(path)
    if im.dtype == np.uint16:
        im = (im / 257).astype(np.uint8)
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    if im.shape[2] == 4:
        im = cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)
    else:
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
        im = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
    return im.astype(np.float32) / 255.0


def fit_rgba(im, w, h, fit="cover", anchor=(0.5, 0.5), bg=None):
    """Fit an RGBA image into w x h. cover = fill and crop at anchor; contain = letterbox on bg (or transparent)."""
    h0, w0 = im.shape[:2]
    s = max(w / w0, h / h0) if fit == "cover" else min(w / w0, h / h0)
    nw, nh = max(1, int(round(w0 * s))), max(1, int(round(h0 * s)))
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4
    r = cv2.resize(im, (nw, nh), interpolation=interp)
    if fit == "cover":
        x = int(round((nw - w) * anchor[0]))
        y = int(round((nh - h) * anchor[1]))
        return np.clip(r[y:y + h, x:x + w], 0, 1)
    out = np.zeros((h, w, 4), np.float32)
    if bg is not None:
        out[..., :3] = color(bg)
        out[..., 3] = 1.0
    x, y = (w - nw) // 2, (h - nh) // 2
    a = r[..., 3:4]
    out[y:y + nh, x:x + nw, :3] = out[y:y + nh, x:x + nw, :3] * (1 - a) + r[..., :3] * a
    out[y:y + nh, x:x + nw, 3] = np.maximum(out[y:y + nh, x:x + nw, 3], r[..., 3])
    return np.clip(out, 0, 1)


# ------------------------------------------------------------------ clip cache / decoder
class ClipCache:
    """Decodes source clips (ffmpeg pipe -> uint8 RAM), already scaled/cropped to the output size.

    Frames are fetched by source time with NEAREST-frame sampling (blending caused ghosting on retimes).
    `want` = {path: (src_lo, src_hi)} restricts decoding to the source range the cut actually samples
    (plus a margin), which keeps RAM and decode time proportional to what is used; a request outside the
    decoded range re-decodes the union. LRU bounded by `budget_mb` (always keeps >= 2 clips so a transition
    between two clips never thrashes). Still images are cached as a single frame.
    """

    def __init__(self, budget_mb=4000, want=None, margin_s=0.25, untagged_matrix="bt709"):
        self.untagged_matrix = untagged_matrix
        self.store = OrderedDict()
        self.budget = budget_mb * 1024 * 1024
        self.want = want or {}
        self.margin = margin_s

    def _bytes(self):
        return sum(v[0].nbytes for v in self.store.values())

    def _decode(self, path, w, h, fit, anchor, bg, rng=None):
        """Returns (frames uint8 (n,h,w,3), fps, first_index, reaches_clip_end)."""
        if is_image(path):
            im = fit_rgba(load_rgba(path), w, h, fit, anchor, bg or "#000000")
            return (np.clip(im[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)[None], 1.0, 0, True
        info = probe(path)
        fps = info["fps"]
        # untagged HD sources are BT.709 by convention (ffmpeg would otherwise assume BT.601 when converting
        # to RGB). Override with the cut list's "source_matrix" ("bt709" | "bt601" | "auto") if colours shift.
        um = self.untagged_matrix
        if um == "bt709" and info["height"] < 720:
            um = "auto"
        cm = um if info["color_space"] in ("unknown", "", None) else "auto"
        if fit == "contain":
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos:in_color_matrix={cm},"
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={(bg or '#000000').replace('#', '0x')}")
        else:
            ax, ay = anchor
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos:in_color_matrix={cm},"
                  f"crop={w}:{h}:(iw-{w})*{ax}:(ih-{h})*{ay}")
        first, total = 0, int(math.ceil((info["duration"] or 10.0) * fps)) + 4
        pre = f"fps={fps:.6f},"
        if rng is not None:
            first = max(0, int(math.floor(rng[0] * fps)) - 1)
            last = int(math.ceil(rng[1] * fps)) + 2
            total = max(1, min(total, last - first))
            # trim on exact frame indices of the CFR stream BEFORE scaling (cheaper, frame-exact)
            pre += f"trim=start_frame={first}:end_frame={first + total},setpts=PTS-STARTPTS,"
        vf = pre + vf + ",format=rgb24"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-vf", vf, "-an",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        # read straight into a preallocated buffer (subprocess capture of ~1 GB is several times slower)
        buf = np.empty((total, h, w, 3), np.uint8)
        extra, n, fsz = [], 0, w * h * 3
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        while True:
            dst = buf[n] if n < total else np.empty((h, w, 3), np.uint8)
            mv = memoryview(dst.reshape(-1))
            got = 0
            while got < fsz:
                k = p.stdout.readinto(mv[got:])
                if not k:
                    break
                got += k
            if got < fsz:
                break
            if n >= total:
                extra.append(dst)
            n += 1
        err = p.stderr.read().decode(errors="replace")
        if p.wait() != 0 or n == 0:
            raise RuntimeError(f"decode failed for {path}: {err.strip() or 'no frames in range'}")
        frames = buf[:n] if not extra else np.concatenate([buf[:total], np.stack(extra)])
        return frames, fps, first, (rng is None or n < total)

    def _load(self, key, rng):
        path, w, h, fit, anchor, bg = key
        arr, fps, first, at_end = self._decode(path, w, h, fit, anchor, bg, rng)
        self.store.pop(key, None)
        while len(self.store) >= 2 and self._bytes() + arr.nbytes > self.budget:
            self.store.popitem(last=False)
        self.store[key] = (arr, fps, first, rng, at_end)
        return self.store[key]

    def get(self, path, t_src, w, h, fit="cover", anchor=(0.5, 0.5), bg=None, blend=False):
        """Frame at source time t_src (seconds). Clamps to the clip (holds first/last frame)."""
        key = (path, w, h, fit, tuple(anchor), bg)
        ent = self.store.get(key)
        if ent is None:
            r = self.want.get(path)
            rng = None if r is None else (min(r[0], t_src) - self.margin, max(r[1], t_src) + self.margin)
            ent = self._load(key, rng)
        else:
            self.store.move_to_end(key)
        fr, fps, first, rng, at_end = ent
        x = max(t_src * fps, 0.0)
        i_near = int(math.floor(x + 0.5 + 1e-6))
        if rng is not None and (i_near < first or (i_near >= first + len(fr) and not at_end)):
            # outside the decoded window: widen it and re-decode
            rng = (min(rng[0], t_src) - self.margin, max(rng[1], t_src) + self.margin)
            fr, fps, first, rng, at_end = self._load(key, rng)
        x = min(max(x - first, 0.0), len(fr) - 1)
        if not blend:
            i = int(math.floor(x + 0.5 + 1e-6))
            return fr[min(i, len(fr) - 1)].astype(np.float32) * (1.0 / 255.0)
        i0 = int(math.floor(x + 1e-6))
        i1 = min(i0 + 1, len(fr) - 1)
        a = x - i0
        f0 = fr[i0].astype(np.float32) * (1.0 / 255.0)
        if a < 1e-3 or i1 == i0:
            return f0
        return f0 * (1 - a) + fr[i1].astype(np.float32) * (a / 255.0)


def tune_malloc():
    """glibc: keep freed large buffers in the heap instead of returning them to the OS. Every frame op
    allocates 6-25 MB temporaries; on VMs where first-touch page faults are slow this ~halves frame time.
    No-op elsewhere."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt(-3, 32 * 1024 * 1024)  # M_MMAP_THRESHOLD (max allowed)
        libc.mallopt(-1, 1 << 30)  # M_TRIM_THRESHOLD
    except Exception:
        pass


# ------------------------------------------------------------------ easing
def clamp01(x):
    return min(max(x, 0.0), 1.0)


def smooth(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def smoother(x):
    x = clamp01(x)
    return x * x * x * (x * (6 * x - 15) + 10)


def ease_in(x, p=2.0):
    return clamp01(x) ** p


def ease_out(x, p=2.0):
    return 1 - (1 - clamp01(x)) ** p


def ease_in_out(x, p=2.0):
    x = clamp01(x)
    return 0.5 * (2 * x) ** p if x < 0.5 else 1 - 0.5 * (2 - 2 * x) ** p


def lerp(a, b, t):
    return a + (b - a) * t


EASE = {"linear": clamp01, "smooth": smooth, "smoother": smoother, "in": ease_in, "out": ease_out,
        "in_out": ease_in_out}


def interp_keys(keys, t, ease="linear"):
    """keys: [[t, v1, v2, ...], ...] sorted by t. Returns [v1, v2, ...] at t (held outside the range).
    ease applies within each key interval ('linear', 'smooth', 'smoother', 'in', 'out', 'in_out')."""
    if t <= keys[0][0]:
        return [float(v) for v in keys[0][1:]]
    if t >= keys[-1][0]:
        return [float(v) for v in keys[-1][1:]]
    for k0, k1 in zip(keys, keys[1:]):
        if k0[0] <= t <= k1[0]:
            span = k1[0] - k0[0]
            u = EASE.get(ease, clamp01)((t - k0[0]) / span if span > 0 else 1.0)
            n = min(len(k0), len(k1))
            return [float(k0[i] + (k1[i] - k0[i]) * u) for i in range(1, n)]
    return [float(v) for v in keys[-1][1:]]


def piecewise(pts, x):
    """Piecewise-linear [[x, y], ...] with linear EXTRAPOLATION past both ends (used for time ramps,
    so transition handles keep moving at the ramp's end speed)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if len(pts) == 1:
        return ys[0] + (x - xs[0])
    if x <= xs[0]:
        return ys[0] + (x - xs[0]) * (ys[1] - ys[0]) / (xs[1] - xs[0])
    if x >= xs[-1]:
        return ys[-1] + (x - xs[-1]) * (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    return float(np.interp(x, xs, ys))


# ------------------------------------------------------------------ image ops
def translate(img, dx, dy=0.0, border=cv2.BORDER_REFLECT):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=border)


def scale_about(img, s, cx=None, cy=None, angle=0.0, dx=0.0, dy=0.0, border=cv2.BORDER_REFLECT):
    """Zoom s and rotate `angle` degrees (CCW positive) about (cx, cy) (default centre), then shift dx, dy px."""
    h, w = img.shape[:2]
    cx = w / 2 if cx is None else cx
    cy = h / 2 if cy is None else cy
    M = cv2.getRotationMatrix2D((cx, cy), angle, s)
    M[0, 2] += dx
    M[1, 2] += dy
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=border)


def motion_blur(img, length, vertical=False):
    L = int(round(abs(length)))
    if L < 2:
        return img
    k = (1, L) if vertical else (L, 1)
    return cv2.blur(img, k, borderType=cv2.BORDER_REFLECT)


def rot_blur(img, angle_span, s=1.0, samples=8, cx=None, cy=None, base_angle=0.0):
    """Rotational motion blur: average `samples` rotations spanning angle_span degrees around base_angle."""
    if abs(angle_span) < 0.3 or samples < 2:
        return scale_about(img, s, cx, cy, base_angle)
    acc = np.zeros_like(img)
    for i in range(samples):
        a = base_angle + angle_span * (i / (samples - 1) - 0.5)
        acc += scale_about(img, s, cx, cy, a)
    return acc / samples


def gauss(img, sigma):
    if sigma <= 0.05:
        return img
    return cv2.GaussianBlur(img, (0, 0), sigma)


def gauss_fast(img, sigma):
    """Large-sigma blur at half resolution (visually identical for glows/shadows, ~4x faster)."""
    if sigma < 6:
        return gauss(img, sigma)
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / 2)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def luma(img):
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def screen(a, b, amt=1.0):
    return 1 - (1 - a) * (1 - np.clip(b * amt, 0, 1))


def mix(a, b, m):
    if np.isscalar(m):
        return a * (1 - m) + b * m
    return a * (1 - m[..., None]) + b * m[..., None]


_GRID = {}


def _dist(w, h, cx, cy):
    key = (w, h)
    if key not in _GRID:
        _GRID.clear()
        _GRID[key] = np.mgrid[0:h, 0:w].astype(np.float32)
    yy, xx = _GRID[key]
    return np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2), xx, yy


def radial_mask(w, h, cx, cy, r, feather):
    d, _, _ = _dist(w, h, cx, cy)
    return np.clip((r - d) / max(feather, 1e-3) + 0.5, 0, 1)


def ripple_edge(img, cx, cy, rad, t, amp=10.0, width=140.0, wavelength=38.0):
    """Concentric refraction ripples riding a spreading circular edge (for water-like reveals)."""
    h, w = img.shape[:2]
    d, xx, yy = _dist(w, h, cx, cy)
    d = d + 1e-3
    band = np.exp(-((d - rad) / width) ** 2) + 0.35 * np.exp(-((d - rad * 0.72) / (width * 1.6)) ** 2)
    off = amp * band * np.sin(d / wavelength * 2 * np.pi - t * 18.0)
    mx = (xx + off * (xx - cx) / d).astype(np.float32)
    my = (yy + off * (yy - cy) / d).astype(np.float32)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def load_matte(path, w, h, soften=2.0):
    """Greyscale PNG matte (white = reveal) fitted (cover) to w x h, lightly softened."""
    m = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if m is None:
        raise FileNotFoundError(path)
    if m.ndim == 3 and m.shape[2] == 4:
        m = m[..., 3]
    elif m.ndim == 3:
        m = cv2.cvtColor(m, cv2.COLOR_BGR2GRAY)
    m = m.astype(np.float32) / (65535.0 if m.dtype == np.uint16 else 255.0)
    m = fit_rgba(np.dstack([m, m, m, np.ones_like(m)]), w, h, "cover")[..., 0]
    return gauss(m, soften)


def over(base, rgba, x0=0, y0=0, opacity=1.0):
    """Composite an RGBA layer (unpremultiplied) onto base at integer offset (x0, y0), clipped to the frame.
    Returns a new array; base is not modified."""
    H, W = base.shape[:2]
    h, w = rgba.shape[:2]
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if xb <= xa or yb <= ya or opacity <= 0:
        return base
    sub = rgba[ya - y0:yb - y0, xa - x0:xb - x0]
    a = sub[..., 3:4] * opacity
    out = base.copy()
    out[ya:yb, xa:xb] = out[ya:yb, xa:xb] * (1 - a) + sub[..., :3] * a
    return out


def multiply_shadow(base, alpha, x0, y0, amount=0.25, sigma=10.0, dx=0.0, dy=0.0, pad=None):
    """Soft, offset-free (by default) multiply shadow from an alpha crop placed at (x0, y0)."""
    if amount <= 0:
        return base
    pad = int(pad if pad is not None else math.ceil(3 * sigma + max(abs(dx), abs(dy))))
    h, w = alpha.shape
    a = np.zeros((h + 2 * pad, w + 2 * pad), np.float32)
    a[pad:pad + h, pad:pad + w] = alpha
    if dx or dy:
        a = translate(a, dx, dy, border=cv2.BORDER_CONSTANT)
    sh = np.clip(gauss(a, sigma) * amount, 0, 1)
    lay = np.zeros(sh.shape + (4,), np.float32)
    lay[..., 3] = sh
    return over(base, lay, x0 - pad, y0 - pad)


def rotate_rgba(s, angle):
    """Rotate an RGBA sprite about its centre (deg, CCW positive) onto a square canvas that fits it."""
    hh, ww = s.shape[:2]
    diag = int(math.ceil(math.hypot(hh, ww)))
    padded = np.zeros((diag, diag, 4), np.float32)
    oy, ox = (diag - hh) // 2, (diag - ww) // 2
    padded[oy:oy + hh, ox:ox + ww] = s
    R = cv2.getRotationMatrix2D((diag / 2, diag / 2), angle, 1.0)
    return cv2.warpAffine(padded, R, (diag, diag), flags=cv2.INTER_LINEAR)


def place(base, sprite_rgba, cx, cy, scale=1.0, angle=0.0, alpha=1.0, blend="normal"):
    """Place an RGBA sprite centred at (cx, cy) with scale, rotation (deg CCW) and opacity."""
    s = sprite_rgba
    if scale != 1.0:
        s = cv2.resize(s, (max(1, int(round(s.shape[1] * scale))), max(1, int(round(s.shape[0] * scale)))),
                       interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    if angle:
        s = rotate_rgba(s, angle)
    x0, y0 = int(round(cx - s.shape[1] / 2)), int(round(cy - s.shape[0] / 2))
    if blend == "screen":
        H, W = base.shape[:2]
        xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + s.shape[1]), min(H, y0 + s.shape[0])
        if xb <= xa or yb <= ya:
            return base
        sub = s[ya - y0:yb - y0, xa - x0:xb - x0]
        out = base.copy()
        out[ya:yb, xa:xb] = screen(out[ya:yb, xa:xb], sub[..., :3] * sub[..., 3:4], alpha)
        return out
    return over(base, s, x0, y0, alpha)


# ------------------------------------------------------------------ grading
DEFAULT_FLOOR = "#1E1F1C"  # a soft near-black; set look.floor in the cut list to taste


def _vec(v):
    return np.array(v, np.float32) if isinstance(v, (list, tuple)) else np.float32(v)


def _ramp(w, h, direction):
    _, xx, yy = _dist(w, h, 0, 0)
    X, Y = xx / w, yy / h
    if direction == "left":
        return np.clip(1 - X * 1.4, 0, 1)
    if direction == "right":
        return np.clip(X * 1.4 - 0.4, 0, 1)
    if direction == "top":
        return np.clip(1 - Y * 1.6, 0, 1)
    if direction == "bottom":
        return np.clip(Y * 1.6 - 0.6, 0, 1)
    return np.ones_like(X)


def grade(img, g, floor=DEFAULT_FLOOR):
    """Primary grade. g keys (all optional, applied in this order):
      exposure (stops) | temp (+warm/-cool) | tint (+magenta/-green) | contrast (pivot 0.45) | sat (+/-, 0 = none)
      lift  (scalar or [r,g,b]; raises blacks: out + lift*(1-out))  | gamma (scalar or rgb) | gain (scalar or rgb)
      milky (0..1; blends toward the look's floor colour, 'never pure black' milky shadows)
      rgb   ([r,g,b] multipliers) | light ({dir: left|right|top|bottom|flat, color, amount}: screen-blended light ramp)
    """
    if not g:
        return img
    out = img
    if g.get("exposure"):
        out = out * np.float32(2.0 ** g["exposure"])
    if g.get("temp"):
        t = g["temp"]
        out = out * np.array([1 + 0.06 * t, 1 + 0.01 * t, 1 - 0.07 * t], np.float32)
    if g.get("tint"):
        t = g["tint"]
        out = out * np.array([1 + 0.03 * t, 1 - 0.04 * t, 1 + 0.03 * t], np.float32)
    if g.get("contrast"):
        out = (out - 0.45) * np.float32(1 + g["contrast"]) + 0.45
    if g.get("sat"):
        l = luma(np.clip(out, 0, 1))[..., None]
        out = l + (out - l) * np.float32(1 + g["sat"])
    if "lift" in g:
        L = _vec(g["lift"])
        out = out + L * (1 - out)
    if "gamma" in g:
        out = np.clip(out, 0, None) ** (1.0 / _vec(g["gamma"]))
    if "gain" in g:
        out = out * _vec(g["gain"])
    if g.get("milky"):
        k = np.float32(g["milky"])
        out = out * (1 - k) + color(g.get("floor", floor)) * k * 1.6
    if "rgb" in g:
        out = out * np.array(g["rgb"], np.float32)
    if g.get("light"):
        lt = g["light"]
        h, w = out.shape[:2]
        ramp = _ramp(w, h, lt.get("dir", "left"))[..., None] * color(lt.get("color", [1.0, 0.75, 0.45]))
        out = screen(np.clip(out, 0, 1), ramp * np.float32(lt.get("amount", 0.25)))
    return np.clip(out, 0, 1).astype(np.float32)


def halation(img, amt=0.05, thresh=0.74, sigma=16, tint=(1.0, 0.82, 0.62)):
    """Warm glow around highlights above `thresh` (film halation)."""
    if amt <= 0:
        return img
    h, w = img.shape[:2]
    f = 4 if sigma >= 12 else (2 if sigma >= 6 else 1)  # the glow is soft: compute it at reduced resolution
    small = cv2.resize(img, (w // f, h // f), interpolation=cv2.INTER_AREA) if f > 1 else img
    m = np.clip((luma(small) - thresh) / (1 - thresh), 0, 1)
    glow = gauss(small * m[..., None], sigma / f) * (np.array(tint, np.float32) * np.float32(amt))
    if f > 1:
        glow = cv2.resize(glow, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(img + glow, 0, 1)


_GRAIN = {}
_GRAIN_BANK = 8


def _grain_plate(w, h, seed, n):
    """Deterministic grain field for frame n: one of a small bank of soft noise plates, rolled by a
    per-frame pseudo-random offset (indistinguishable from fresh noise at 24 fps, ~5x cheaper)."""
    key = (w, h, seed)
    if key not in _GRAIN:
        _GRAIN.clear()
        bank = []
        for i in range(_GRAIN_BANK):
            rng = np.random.default_rng(seed * 7919 + i)
            g = rng.standard_normal((h // 2, w // 2), dtype=np.float32)
            g = cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
            bank.append(gauss(g, 0.6))
        _GRAIN[key] = bank
    rng = np.random.default_rng(seed * 104729 + n)
    dx, dy = int(rng.integers(0, w)), int(rng.integers(0, h))
    return np.roll(_GRAIN[key][n % _GRAIN_BANK], (dy, dx), axis=(0, 1))


def grain(img, n, amt=0.016, seed=0):
    """Deterministic per-frame luminance grain (frame index n), strongest in the mid-tones."""
    if amt <= 0:
        return img
    h, w = img.shape[:2]
    g = _grain_plate(w, h, seed, n)
    l = luma(img)
    wgt = 0.35 + 0.65 * (1 - np.abs(l - 0.45) / 0.55)
    return np.clip(img + (g * np.float32(amt) * wgt)[..., None], 0, 1)


def vignette(img, amt=0.2, roundness=1.0):
    if amt <= 0:
        return img
    h, w = img.shape[:2]
    _, xx, yy = _dist(w, h, 0, 0)
    X = (xx / w - 0.5) * 2
    Y = (yy / h - 0.5) * 2 * roundness
    v = 1 - amt * np.clip((X * X + Y * Y) / 2, 0, 1) ** 1.5
    return img * v[..., None]


# ------------------------------------------------------------------ transition primitives
def whip(a_img, b_img, u, direction="right", max_px=1400, max_blur=420, border=cv2.BORDER_REFLECT):
    """u in [0,1]. Camera pans `direction`; content moves the opposite way.
    A exits accelerating (u<0.5), B arrives decelerating. Tip: on vertical whips use less px, more blur,
    or the reflected border can show as a mirrored band."""
    sign = {"right": -1, "left": 1, "down": -1, "up": 1}[direction]
    vert = direction in ("up", "down")
    if u < 0.5:
        k = ease_in(u / 0.5, 2.2)
        off = sign * max_px * k
        img = translate(a_img, 0 if vert else off, off if vert else 0, border)
        return motion_blur(img, max_blur * k, vertical=vert)
    k = 1 - ease_out((u - 0.5) / 0.5, 2.2)
    off = -sign * max_px * k
    img = translate(b_img, 0 if vert else off, off if vert else 0, border)
    return motion_blur(img, max_blur * k, vertical=vert)


def bloom(img, k, col="#F3ECDF", ceiling=0.93):
    """k 0..1: warm luma lift + halation toward `col`, never clipping to pure white."""
    if k <= 0:
        return img
    h = halation(img, amt=0.5 * k, thresh=0.5, sigma=30)
    lifted = h * (1 - 0.55 * k) + color(col) * 0.55 * k + 0.08 * k
    return np.clip(lifted, 0, ceiling)


def dark(img, k, col=DEFAULT_FLOOR):
    """Sink toward a near-black colour (k 0..1)."""
    if k <= 0:
        return img
    return img * (1 - k) + color(col) * np.float32(k)


# ------------------------------------------------------------------ output
def to_u8(img):
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)


def write_image(path, img, quality=92):
    bgr = cv2.cvtColor(to_u8(img), cv2.COLOR_RGB2BGR)
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if path.lower().endswith((".jpg", ".jpeg")) else []
    cv2.imwrite(path, bgr, params)


class Encoder:
    """rgb24 frames -> ffmpeg -> H.264 yuv420p BT.709 (tagged), +faststart.
    crf (default 16; 12 for finals, 23 for proxies) or bitrate (e.g. '40M') which overrides crf."""

    def __init__(self, out_path, w, h, fps=24, crf=16, bitrate=None, preset="medium", codec="libx264"):
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        rate = ["-b:v", str(bitrate), "-maxrate", str(bitrate), "-bufsize", str(bitrate)] if bitrate else ["-crf", str(crf)]
        self.cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                    "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-c:v", codec, "-preset", preset] + rate + [
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                    "-movflags", "+faststart", out_path]
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE)
        self.n = 0

    def write(self, img):
        self.p.stdin.write(to_u8(img).tobytes())
        self.n += 1

    def close(self):
        self.p.stdin.close()
        rc = self.p.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg encoder exited {rc}: {' '.join(self.cmd)}")
