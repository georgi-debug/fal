"""Generic UI-device overlays: a typed "prompt pill" and a cursor sprite with a click spin + pulse ring.

Everything is config-driven (no brand assets):
  cursor : {"image": "assets/cursor.png" | null (null = a drawn ring), "size": 40 (px width),
            "color": "#FFFFFF" (ring/pulse colour), "hotspot": [0.5, 0.5] (fraction of the sprite that sits
            on the path point and is the spin centre), "spin_deg": 360 (one CCW turn per click; 0 = none),
            "click_dur": 0.34, "pulse": true, "shadow": 0.35}
  pill   : {"style": "pill" (typo style name or dict), "height": 60, "pad_x": 24, "radius": null (= height/2),
            "fill": "#000000", "fill_opacity": 0.32, "outline": "#FFFFFF", "outline_opacity": 0.92,
            "outline_width": 2, "grow": false (true = pill grows with the typed text)}
The spin curve is 360*(1-(1-u)^3): fast start, eased stop.
"""
import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

import engine as E
import typo as T


def spin_curve(u, deg=360.0):
    u = E.clamp01(u)
    return deg * (1 - (1 - u) ** 3)


_SPRITES = {}


def _ring_sprite(size, col):
    s = int(size * T.SS)
    im = Image.new("L", (s + 4, s + 4), 0)
    d = ImageDraw.Draw(im)
    wid = max(2, int(s * 0.18))
    d.ellipse([2, 2, s + 1, s + 1], outline=255, width=wid)
    a = np.asarray(im.resize((int(size) + 2, int(size) + 2), Image.LANCZOS), np.float32) / 255.0
    rgba = np.zeros(a.shape + (4,), np.float32)
    rgba[..., :3] = E.color(col)
    rgba[..., 3] = a
    return rgba


def cursor_sprite(cfg, resolve):
    key = (cfg.get("image"), cfg.get("size", 40), str(cfg.get("color")), tuple(cfg.get("hotspot", (0.5, 0.5))))
    if key in _SPRITES:
        return _SPRITES[key]
    size = cfg.get("size", 40)
    if cfg.get("image"):
        im = E.load_rgba(resolve(cfg["image"]))
        s = size / im.shape[1]
        im = cv2.resize(im, (max(1, int(round(im.shape[1] * s))), max(1, int(round(im.shape[0] * s)))),
                        interpolation=cv2.INTER_AREA)
    else:
        im = _ring_sprite(size, cfg.get("color", "#FFFFFF"))
    # pad so the hotspot is the sprite centre (place() and rotation work about the centre)
    hx, hy = cfg.get("hotspot", (0.5, 0.5))
    h, w = im.shape[:2]
    px, py = hx * w, hy * h
    half_w = int(math.ceil(max(px, w - px))) + 2
    half_h = int(math.ceil(max(py, h - py))) + 2
    out = np.zeros((2 * half_h, 2 * half_w, 4), np.float32)
    ox, oy = int(round(half_w - px)), int(round(half_h - py))
    out[oy:oy + h, ox:ox + w] = im
    _SPRITES[key] = out
    return out


def draw_cursor(img, cfg, x, y, t, clicks, opacity, resolve):
    spr = cursor_sprite(cfg, resolve)
    ang, click = 0.0, 0.0
    dur = cfg.get("click_dur", 0.34)
    for c in clicks:
        if c <= t < c + dur:
            u = (t - c) / dur
            ang = spin_curve(u, cfg.get("spin_deg", 360.0))
            click = u
    if abs(ang) > 0.01:
        spr = E.rotate_rgba(spr, ang)
    shadow = cfg.get("shadow", 0.35)
    if shadow > 0:
        img = E.multiply_shadow(img, spr[..., 3] * opacity, int(round(x - spr.shape[1] / 2)),
                                int(round(y - spr.shape[0] / 2)), shadow, 3.0)
    img = E.place(img, spr, x, y, 1.0, 0.0, opacity)
    if click > 0 and cfg.get("pulse", True):
        size = cfg.get("size", 40)
        r = size * (0.6 + 1.2 * click)
        R = int(math.ceil(r + 4))
        yy, xx = np.mgrid[-R:R + 1, -R:R + 1].astype(np.float32)
        ring = np.clip(1 - np.abs(np.sqrt(xx * xx + yy * yy) - r) / 1.6, 0, 1) * (1 - click) * 0.8
        lay = np.zeros(ring.shape + (4,), np.float32)
        lay[..., :3] = E.color(cfg.get("color", "#FFFFFF"))
        lay[..., 3] = ring
        img = E.over(img, lay, int(round(x)) - R, int(round(y)) - R, opacity)
    return img


def draw_pill(img, ts, x, y, text, chars, cfg, opacity=1.0, caret=True):
    """Rounded prompt pill, (x, y) = left-centre. Types `chars` characters of text; caret only while typing."""
    st = ts.style(cfg.get("style", "pill"))
    h = cfg.get("height", int(st["size"] * 1.6))
    pad = cfg.get("pad_x", int(st["size"] * 0.63))
    n = max(0, int(chars))
    shown = text if not cfg.get("grow") else text[:n]
    tw = ts.ink_width(shown, st) if shown.strip() else 0
    w = int(tw + 2 * pad + st["size"] * 0.37)
    rad = cfg.get("radius", h / 2)
    lw = cfg.get("outline_width", 2)
    S = T.SS
    cw, chh = w + 4, int(h) + 4
    fill = Image.new("L", (cw * S, chh * S), 0)
    line = Image.new("L", (cw * S, chh * S), 0)
    box = [2 * S, 2 * S, (2 + w) * S, (2 + h) * S]
    ImageDraw.Draw(fill).rounded_rectangle(box, radius=rad * S, fill=255)
    if lw > 0:
        ImageDraw.Draw(line).rounded_rectangle(box, radius=rad * S, outline=255, width=int(lw * S))
    f = np.asarray(fill.resize((cw, chh), Image.LANCZOS), np.float32) / 255.0
    o = np.asarray(line.resize((cw, chh), Image.LANCZOS), np.float32) / 255.0
    ox, oy = int(round(x)) - 2, int(round(y - h / 2)) - 2
    lay = np.zeros((chh, cw, 4), np.float32)
    lay[..., :3] = E.color(cfg.get("fill", "#000000"))
    lay[..., 3] = f * cfg.get("fill_opacity", 0.32)
    img = E.over(img, lay, ox, oy, opacity)
    lay = np.zeros((chh, cw, 4), np.float32)
    lay[..., :3] = E.color(cfg.get("outline", "#FFFFFF"))
    lay[..., 3] = o * cfg.get("outline_opacity", 0.92)
    img = E.over(img, lay, ox, oy, opacity)
    cap = ts.cap_height(st)
    piece = ts.line_alpha(text, st, x + pad, y + cap / 2, "left", upto=min(n, len(text)),
                          caret=caret and n < len(text))
    return ts.draw(img, [piece], dict(st, shadow=None), opacity)
