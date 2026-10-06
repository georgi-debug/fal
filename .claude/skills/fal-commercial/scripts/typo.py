"""Typography for the cut-list renderer (PIL, 2x supersampled, ink-accurate alignment).

Lessons carried over from production:
  * A VARIABLE font opens at its default instance, which is often the LIGHTEST weight. Always set the
    weight: every style here applies `wght` (default 600) through set_variation_by_axes.
  * Calibrate size/weight against the reference's ink pixels (cap height, stem width, ink extents),
    not against the font's nominal size.
  * Alignment is by INK, not by advance width: a left caption's first glyph's ink starts exactly at
    the margin, a right caption's last glyph's ink ends exactly at W - margin, a title's cap block
    is centred on y.
  * Typed-on text is laid out at its FINAL position (it does not drift while typing) and shows a
    caret only while typing; a caret left after the last letter reads as an extra letter.

Config (all px; the font path always comes from config, never hard-coded):
  font spec : {"path": "fonts/MyFont-VF.ttf", "wght": 600, "axes": {"opsz": 32}, "index": 0}
  style     : {"font": "main" | {font spec}, "size": 67, "wght": 600, "track": -0.025 (em),
               "color": "#FFFFFF", "shadow": {"amount": 0.25, "sigma": 9, "dx": 0, "dy": 0} | null,
               "leading": 1.15 (x size, for stacked lines)}
"""
import math
import sys
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import engine as E

SS = 2  # supersample factor
_AXIS_TAG = {"weight": "wght", "width": "wdth", "optical size": "opsz", "optical": "opsz", "opticalsize": "opsz",
             "slant": "slnt", "italic": "ital", "grade": "GRAD"}
_WARNED = set()


def _warn(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        print("typo: " + msg, file=sys.stderr)


class Typesetter:
    def __init__(self, fonts=None, styles=None, frame=(1920, 1080), resolve=lambda p: p):
        """fonts: {name: font spec}; styles: {name: style}; resolve: maps relative paths to absolute."""
        self.fonts = fonts or {}
        self.styles = styles or {}
        self.W, self.H = frame
        self.resolve = resolve
        self._fcache = {}
        self._lcache = OrderedDict()
        self._layouts = {}

    # ------------------------------------------------------------ fonts
    def font_spec(self, style):
        f = style.get("font", "main")
        if isinstance(f, dict):
            spec = dict(f)
        elif f in self.fonts:
            spec = dict(self.fonts[f])
        elif isinstance(f, str) and f.lower().endswith((".ttf", ".otf", ".ttc")):
            spec = {"path": f}
        else:
            raise ValueError(f"typo: font '{f}' is not defined. Add it to the cut list's \"fonts\" "
                             f"(e.g. \"fonts\": {{\"main\": {{\"path\": \"fonts/YourFont.ttf\", \"wght\": 600}}}}).")
        if "wght" in style:
            spec["wght"] = style["wght"]
        return spec

    def font(self, spec, px):
        """PIL font at px (already supersampled) with variation axes applied."""
        axes = dict(spec.get("axes", {}))
        wght = spec.get("wght", 600)
        key = (spec["path"], int(round(px)), wght, tuple(sorted(axes.items())), spec.get("index", 0))
        if key in self._fcache:
            return self._fcache[key]
        path = self.resolve(spec["path"])
        f = ImageFont.truetype(path, int(round(px)), index=spec.get("index", 0))
        try:
            info = f.get_variation_axes()
        except Exception:
            info = None
        if info:
            vals = []
            for ax in info:
                name = ax["name"].decode() if isinstance(ax["name"], bytes) else str(ax["name"])
                tag = _AXIS_TAG.get(name.strip().lower(), name)
                if tag == "wght":
                    v = axes.get("wght", wght)
                else:
                    v = axes.get(tag, ax["default"])
                vals.append(min(max(v, ax["minimum"]), ax["maximum"]))
            f.set_variation_by_axes(vals)
        elif "wght" in spec or axes:
            _warn(f"{path} is a static font; 'wght'/'axes' are ignored (pick the static file of the weight you want)")
        self._fcache[key] = f
        return f

    def style(self, name_or_dict, **override):
        if isinstance(name_or_dict, dict):
            st = dict(self.styles.get(name_or_dict.get("base", ""), {}))
            st.update(name_or_dict)
        else:
            st = dict(self.styles.get(name_or_dict, {}))
        st.update({k: v for k, v in override.items() if v is not None})
        st.setdefault("size", 64)
        st.setdefault("track", -0.02)
        st.setdefault("color", "#FFFFFF")
        return st

    # ------------------------------------------------------------ layout
    def layout(self, text, st):
        """Pen positions and INK box (supersampled px, relative to the pen origin on the baseline). Cached.
        The ink box is measured on the rasterised glyphs (50% coverage edge): PIL's textbbox x-extents are
        advance-based (they include side bearings), which would misplace margins by several px."""
        key = (text, repr(sorted((k, str(v)) for k, v in st.items() if k not in ("shadow", "color"))))
        hit = self._layouts.get(key)
        if hit is not None:
            return hit
        L = self._layout(text, st)
        if len(self._layouts) > 512:
            self._layouts.clear()
        self._layouts[key] = L
        return L

    def _layout(self, text, st):
        spec = self.font_spec(st)
        size = st["size"]
        f = self.font(spec, size * SS)
        tr = st.get("track", 0.0) * size * SS
        n = len(text)
        if tr == 0:
            # pen before char i, including kerning with the previous char
            pens = [f.getlength(text[:i + 1]) - f.getlength(text[i]) if i < n else f.getlength(text) for i in range(n + 1)]
        else:
            pens = [(f.getlength(text[:i + 1]) - f.getlength(text[i]) if i < n else f.getlength(text)) + i * tr
                    for i in range(n + 1)]
        d = ImageDraw.Draw(Image.new("L", (1, 1)))
        x0 = y0 = 1e9
        x1 = y1 = -1e9
        for i, c in enumerate(text):
            if c.isspace():
                continue
            l, t, r, b = d.textbbox((pens[i], 0), c, font=f, anchor="ls")
            x0, y0, x1, y1 = min(x0, l), min(y0, t), max(x1, r), max(y1, b)
        cap = -d.textbbox((0, 0), "H", font=f, anchor="ls")[1]
        if x0 > x1:  # empty / whitespace
            return dict(font=f, pens=pens, ink=(0, -cap, 0, 0), cap=cap, track=tr)
        # rasterise to find the true ink extents (horizontal AND vertical)
        m = 8
        ox, oy = int(math.floor(x0)) - m, int(math.floor(y0)) - m
        im = Image.new("L", (int(math.ceil(x1)) - ox + m, int(math.ceil(y1)) - oy + m), 0)
        dr = ImageDraw.Draw(im)
        for i, c in enumerate(text):
            if not c.isspace():
                dr.text((pens[i] - ox, -oy), c, font=f, fill=255, anchor="ls")
        a = np.asarray(im) >= 128
        cols, rows = np.where(a.any(0))[0], np.where(a.any(1))[0]
        if len(cols):
            x0, x1 = cols[0] + ox, cols[-1] + 1 + ox
            y0, y1 = rows[0] + oy, rows[-1] + 1 + oy
        return dict(font=f, pens=pens, ink=(x0, y0, x1, y1), cap=cap, track=tr)

    def cap_height(self, st):
        return self.layout("H", st)["cap"] / SS

    def ink_width(self, text, st):
        x0, _, x1, _ = self.layout(text, st)["ink"]
        return (x1 - x0) / SS

    def line_alpha(self, text, st, x, baseline, align="center", upto=None, caret=False):
        """Render one line. x = ink left (align left) | ink right (right) | ink centre (center).
        upto = number of characters visible (typed-on); caret draws a bar after the last visible char.
        Returns (alpha float32 (h, w), ox, oy) in 1x frame px (cached)."""
        key = (text, repr(sorted((k, str(v)) for k, v in st.items() if k != "shadow")), round(x * SS), round(baseline * SS),
               align, upto, caret)
        if key in self._lcache:
            self._lcache.move_to_end(key)
            return self._lcache[key]
        L = self.layout(text, st)
        f, pens, (ix0, iy0, ix1, iy1), cap = L["font"], L["pens"], L["ink"], L["cap"]
        inkw = (ix1 - ix0) / SS
        ink_left = x if align == "left" else (x - inkw if align == "right" else x - inkw / 2)
        pen_x = ink_left - ix0 / SS  # 1x px of pen origin
        size = st["size"]
        pad = 4 + int(size * 0.05)
        extra = int(size * 0.35) if caret or upto is not None else 0
        ox = int(math.floor(ink_left - pad))
        oy = int(math.floor(baseline + min(iy0, -cap * 1.1) / SS - pad))
        cw = int(math.ceil(ink_left + inkw + pad + extra)) - ox
        ch = int(math.ceil(baseline + max(iy1, cap * 0.15) / SS + pad)) - oy
        im = Image.new("L", (cw * SS, ch * SS), 0)
        d = ImageDraw.Draw(im)
        px0 = (pen_x - ox) * SS
        by = (baseline - oy) * SS
        n = len(text) if upto is None else max(0, min(len(text), int(upto)))
        if L["track"] == 0 and n == len(text):
            d.text((px0, by), text, font=f, fill=255, anchor="ls")
        else:
            for i in range(n):
                if not text[i].isspace():
                    d.text((px0 + pens[i], by), text[i], font=f, fill=255, anchor="ls")
        if caret:
            cx = px0 + pens[n] + size * SS * 0.04
            cwid = max(SS * 2, size * SS * 0.06)
            d.rectangle([cx, by - cap * 1.08, cx + cwid, by + cap * 0.14], fill=255)
        a = np.asarray(im.resize((cw, ch), Image.LANCZOS), np.float32) / 255.0
        res = (a, ox, oy)
        self._lcache[key] = res
        while len(self._lcache) > 256:
            self._lcache.popitem(last=False)
        return res

    # ------------------------------------------------------------ compositing
    def draw(self, img, pieces, st, opacity=1.0):
        """pieces: list of (alpha, ox, oy). Applies the style's soft shadow, then the text colour."""
        if opacity <= 0:
            return img
        sh = st.get("shadow")
        if sh and sh.get("amount", 0) > 0:
            for a, ox, oy in pieces:
                img = E.multiply_shadow(img, a * opacity, ox, oy, sh.get("amount", 0.22), sh.get("sigma", 10),
                                        sh.get("dx", 0), sh.get("dy", 0))
        col = E.color(st.get("color", "#FFFFFF"))
        for a, ox, oy in pieces:
            lay = np.empty(a.shape + (4,), np.float32)
            lay[..., :3] = col
            lay[..., 3] = a
            img = E.over(img, lay, ox, oy, opacity)
        return img

    # ------------------------------------------------------------ layouts
    def block(self, img, lines, st, x=None, y=None, align="center", opacity=1.0, chars=None, caret=None):
        """Lines stacked with `leading` (x size), the whole cap block centred on y; x per align (ink).
        chars = visible characters counted across lines (typed-on); caret defaults to 'while typing'."""
        W, H = img.shape[1], img.shape[0]
        x = W / 2 if x is None else x
        y = H / 2 if y is None else y
        cap = self.cap_height(st)
        lead = st.get("leading", 1.15) * st["size"]
        n = len(lines)
        first_base = y - ((n - 1) * lead + cap) / 2 + cap
        total = sum(len(s) for s in lines)
        typing = chars is not None and chars < total
        if caret is None:
            caret = typing
        pieces = []
        if chars is None:
            for i, s in enumerate(lines):
                pieces.append(self.line_alpha(s, st, x, first_base + i * lead, align))
        else:
            left = max(0, int(chars))
            for i, s in enumerate(lines):
                upto = min(len(s), left)
                left -= upto
                # the caret sits on the line currently being typed
                pieces.append(self.line_alpha(s, st, x, first_base + i * lead, align, upto, bool(caret) and left == 0))
                if left == 0:
                    break
        return self.draw(img, pieces, st, opacity)

    def title(self, img, text, st, x=None, y=None, opacity=1.0):
        return self.block(img, text.split("\n"), st, x, y, "center", opacity)

    def split(self, img, left, right, st, margin=60, baseline=None, opacity=1.0):
        """Split caption: left half's ink starts at x=margin, right half's ink ends at W-margin, same baseline."""
        W, H = img.shape[1], img.shape[0]
        baseline = round(H * 0.551) if baseline is None else baseline
        pieces = []
        if left:
            pieces.append(self.line_alpha(left, st, margin, baseline, "left"))
        if right:
            pieces.append(self.line_alpha(right, st, W - margin, baseline, "right"))
        return self.draw(img, pieces, st, opacity)

    def parting(self, img, text, st, u, x=None, y=None, scale_to=3.0, travel=None, blur=60):
        """Title exit: first word leaves left, the rest leaves right, both scaling up with motion blur (u 0..1)."""
        W, H = img.shape[1], img.shape[0]
        if u >= 1:
            return img
        words = text.split(" ")
        if len(words) < 2:
            return self.title(img, text, st, x, y, 1 - E.smooth(u))
        x = W / 2 if x is None else x
        y = H / 2 if y is None else y
        travel = W * 0.47 if travel is None else travel
        left_w, right_w = words[0], " ".join(words[1:])
        cap = self.cap_height(st)
        base_y = y + cap / 2
        full = self.ink_width(text, st)
        ink_left = x - full / 2
        ink_right = x + full / 2
        sc = 1 + (scale_to - 1) * u
        sst = dict(st, size=st["size"] * sc)
        bl = (base_y - y) * sc + y
        pieces = [self.line_alpha(left_w, sst, (ink_left - x) * sc + x - travel * u, bl, "left"),
                  self.line_alpha(right_w, sst, (ink_right - x) * sc + x + travel * u, bl, "right")]
        if u > 0.05 and blur > 0:
            p = int(blur * u / 2) + 2
            pieces = [(E.motion_blur(np.pad(a, ((0, 0), (p, p))), blur * u), ox - p, oy) for a, ox, oy in pieces]
        return self.draw(img, pieces, dict(st, shadow=None))
