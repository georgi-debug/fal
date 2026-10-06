#!/usr/bin/env python3
"""Cut-list renderer: cutlist.json -> frame-accurate silent H.264 master.

  python render.py comp/cutlist.json --out comp/out/cut_v1.mp4            # full render
  python render.py comp/cutlist.json --out comp/out/x.mp4 --from 10 --to 14   # partial range
  python render.py comp/cutlist.json --stills 1.0,5.5,12.25 [--stills-dir DIR]  # review frames (PNG)
  python render.py comp/cutlist.json --check                               # validate + timeline report only
  python render.py comp/cutlist.json --out final.mp4 --crf 12 --jobs 3     # parallel chunks, concatenated

Format: ../reference/cutlist-format.md. Each revision = edit the JSON, re-render. Paths inside the cut list
are relative to the cut list's folder (or its "root"). Frame n is rendered at t = n / fps.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import engine as E  # noqa: E402
import typo as T  # noqa: E402
import ui as U  # noqa: E402

# which side of a transition window each kind actually samples (for handle checks)
_BOTH = {"dissolve", "reveal"}


def default_styles(W, H):
    m = min(W, H)
    return {
        "title": {"size": round(m * 0.15), "wght": 600, "track": -0.02, "shadow": {"amount": 0.18, "sigma": 14}},
        "caption": {"size": round(m * 0.062), "wght": 600, "track": -0.025, "shadow": {"amount": 0.25, "sigma": 9}},
        "text": {"size": round(m * 0.105), "wght": 600, "track": -0.02, "shadow": {"amount": 0.22, "sigma": 12}},
        "pill": {"size": round(m * 0.035), "wght": 600, "track": -0.01},
    }


# ====================================================================== cut list
class Cut:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        with open(self.path) as f:
            c = json.load(f)
        self.c = c
        base = os.path.dirname(self.path)
        self.root = os.path.normpath(os.path.join(base, c.get("root", ".")))
        self.W = int(c.get("width", 1920))
        self.H = int(c.get("height", 1080))
        self.fps = float(c.get("fps", 24))
        self.look = c.get("look", {})
        self.floor = self.look.get("floor", E.DEFAULT_FLOOR)
        self.bloom_color = self.look.get("bloom_color", "#F3ECDF")
        self.finish = c.get("finish", {})
        self.warnings = []
        # ---- segments: t0/t1 explicit, or sequential with dur
        segs, t = [], 0.0
        for i, s in enumerate(c.get("segments", [])):
            s = dict(s)
            s.setdefault("id", f"s{i}")
            s["t0"] = float(s.get("t0", t))
            if "t1" not in s:
                if "dur" not in s:
                    raise ValueError(f"segment {s['id']}: give t1 or dur")
                s["t1"] = s["t0"] + float(s["dur"])
            if "src" in s and "in" not in s:
                s["in"] = s["src"]
            if "out" in s and "ramp" not in s and "speed" not in s:
                s["speed"] = (float(s["out"]) - float(s.get("in", 0))) / (s["t1"] - s["t0"])
            s["path"] = self.P(s["path"])
            if s.get("reveal"):
                rv = s["reveal"] = dict(s["reveal"])
                rv["path"] = self.P(rv.get("path", s["path"]))
                if rv.get("matte"):
                    rv["matte"] = self.P(rv["matte"])
            t = s["t1"]
            segs.append(s)
        if not segs:
            raise ValueError("cut list has no segments")
        self.segs = segs
        self.seg_by = {s["id"]: s for s in segs}
        # ---- transitions
        trs = []
        for tr in c.get("transitions", []):
            tr = dict(tr)
            for k in ("a", "b"):
                if tr.get(k) not in self.seg_by:
                    raise ValueError(f"transition {tr}: unknown segment id '{tr.get(k)}'")
            tr["t"] = float(tr.get("t", self.seg_by[tr["b"]]["t0"]))
            tr.setdefault("hw", 0.0 if tr["kind"] == "cut" else 0.125)
            if tr.get("element"):
                tr["element"] = self.P(tr["element"])
            if tr.get("matte"):
                tr["matte"] = self.P(tr["matte"])
            trs.append(tr)
        self.trs = sorted(trs, key=lambda x: x["t"])
        # ---- overlays
        ovs = []
        for ov in c.get("overlays", []):
            ov = dict(ov)
            for k in ("path", "image"):
                if isinstance(ov.get(k), str):
                    ov[k] = self.P(ov[k])
            ovs.append(ov)
        self.ovs = ovs
        self.duration = float(c.get("duration", max(s["t1"] for s in segs)))
        # ---- typography
        styles = default_styles(self.W, self.H)
        for k, v in c.get("styles", {}).items():
            styles[k] = {**styles.get(k, {}), **v}
        fonts = dict(c.get("fonts", {}))
        if fonts and "main" not in fonts:
            fonts["main"] = next(iter(fonts.values()))
        self.ts = T.Typesetter(fonts, styles, (self.W, self.H), self.P)
        self.ui = c.get("ui", {})

    def P(self, p):
        if p is None or os.path.isabs(p):
            return p
        return os.path.normpath(os.path.join(self.root, p))


# ====================================================================== renderer
def seg_extent(cut, s):
    """Timeline range [lo, hi] at which segment s is actually sampled, including transition handles."""
    lo, hi = s["t0"], s["t1"] - 1 / cut.fps
    for tr in cut.trs:
        if tr["kind"] == "cut" or tr["hw"] <= 0:
            continue
        both = tr["kind"] in _BOTH
        if tr["a"] == s["id"]:
            hi = max(hi, tr["t"] + tr["hw"] if both else tr["t"] - 1e-6)
        if tr["b"] == s["id"]:
            lo = min(lo, tr["t"] - tr["hw"] if both else tr["t"])
    return lo, hi


def source_needs(cut, t0, t1):
    """{path: (src_lo, src_hi)} source seconds sampled while rendering timeline [t0, t1)."""
    want, fps = {}, cut.fps

    def add(path, vals):
        if vals:
            lo, hi = want.get(path, (1e18, -1e18))
            want[path] = (min(lo, min(vals)), max(hi, max(vals)))

    def frames(a, b):
        a, b = max(a, t0), min(b, t1 - 1e-9)
        return [n / fps for n in range(int(math.ceil(a * fps - 1e-6)), int(math.floor(b * fps + 1e-6)) + 1)] if b >= a else []

    for s in cut.segs:
        lo, hi = seg_extent(cut, s)
        ts = frames(lo, hi)
        add(s["path"], [Renderer.src_time(s, t) for t in ts])
        rv = s.get("reveal")
        if rv:
            add(rv["path"], [float(rv.get("in", 0)) + (t - rv["t0"]) * float(rv.get("speed", 1.0))
                             for t in ts if t >= rv["t0"]])
    for ov in cut.ovs:
        p = ov.get("path")
        if ov["kind"] in ("video", "card") and isinstance(p, str) and not E.is_image(p):
            if ov.get("loop"):
                want[p] = (0.0, 1e9)
                continue
            add(p, [float(ov.get("in", 0)) + (t - ov["t0"]) * float(ov.get("speed", 1.0))
                    for t in frames(ov["t0"], ov["t1"])])
    return want


class Renderer:
    def __init__(self, cut, cache_mb=4000, t_range=None):
        self.cut = cut
        self.W, self.H, self.fps = cut.W, cut.H, cut.fps
        t0, t1 = t_range or (0.0, cut.duration)
        self.cache = E.ClipCache(cache_mb, source_needs(cut, t0, t1),
                                 untagged_matrix=cut.c.get("source_matrix", "bt709"))
        self.stills = {}
        self.mattes = {}

    # ------------------------------------------------------------ media
    def clip(self, path, t_src, fit="cover", anchor=(0.5, 0.5), bg=None, blend=False, size=None):
        w, h = size or (self.W, self.H)
        return self.cache.get(path, t_src, w, h, fit, tuple(anchor), bg, blend)

    def still(self, path, width=None):
        key = (path, width)
        if key not in self.stills:
            im = E.load_rgba(path)
            if width:
                import cv2
                s = width / im.shape[1]
                im = cv2.resize(im, (max(1, int(round(im.shape[1] * s))), max(1, int(round(im.shape[0] * s)))),
                                interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4)
            self.stills[key] = im
        return self.stills[key]

    def matte(self, path):
        if path not in self.mattes:
            self.mattes[path] = E.load_matte(path, self.W, self.H)
        return self.mattes[path]

    # ------------------------------------------------------------ segments
    @staticmethod
    def src_time(seg, t):
        """Timeline t -> source seconds: piecewise ramp [[seg_t, src_t], ...] (extrapolated) or in + lt*speed."""
        lt = t - seg["t0"]
        if seg.get("ramp"):
            return E.piecewise(seg["ramp"], lt)
        return float(seg.get("in", 0.0)) + lt * float(seg.get("speed", 1.0))

    def frame_of(self, seg, t, raw=False):
        img = self.clip(seg["path"], self.src_time(seg, t), seg.get("fit", "cover"), seg.get("anchor", (0.5, 0.5)),
                        seg.get("bg"), seg.get("blend", False))
        lt = t - seg["t0"]
        if seg.get("frame") and not raw:  # [[seg_t, scale, dx, dy, angle], ...]
            sc, dx, dy, *rest = E.interp_keys(seg["frame"], lt, seg.get("frame_ease", "linear")) + [0.0, 0.0, 0.0]
            an = rest[0] if rest else 0.0
            if abs(sc - 1) > 1e-4 or abs(dx) > 0.05 or abs(dy) > 0.05 or abs(an) > 0.01:
                img = E.scale_about(img, sc, angle=an, dx=dx, dy=dy)
        rv = seg.get("reveal")
        if rv and t >= rv["t0"]:
            other = self.clip(rv["path"], float(rv.get("in", 0)) + (t - rv["t0"]) * float(rv.get("speed", 1.0)),
                              seg.get("fit", "cover"), seg.get("anchor", (0.5, 0.5)))
            u = E.ease_out((t - rv["t0"]) / max(1e-6, rv["t1"] - rv["t0"]), rv.get("ease_p", 2.0))
            cx, cy = rv.get("cx", self.W / 2), rv.get("cy", self.H / 2)
            rad = rv.get("r0", 40) + u * rv.get("r", math.hypot(self.W, self.H))
            m = E.radial_mask(self.W, self.H, cx, cy, rad, rv.get("feather", 90))
            if rv.get("ripple"):
                other = E.ripple_edge(other, cx, cy, rad, t, rv["ripple"])
            if rv.get("matte"):
                m = m * self.matte(rv["matte"])
            img = E.mix(img, other, m)
        for jt, px in seg.get("jolt", []):  # damped vertical camera bump at absolute time jt
            dt = t - jt
            if 0 <= dt < 0.4:
                img = E.translate(img, 0, px * math.exp(-dt * 12) * math.cos(dt * 60))
        if seg.get("flip"):
            img = np.ascontiguousarray(img[:, ::-1])
        if seg.get("grade"):
            img = E.grade(img, seg["grade"], self.cut.floor)
        return img

    def seg_at(self, t):
        for s in self.cut.segs:
            if s["t0"] - 1e-9 <= t < s["t1"] - 1e-9:
                return s
        return None

    # ------------------------------------------------------------ transitions
    def transition(self, tr, t):
        sa, sb = self.cut.seg_by[tr["a"]], self.cut.seg_by[tr["b"]]
        tc, hw = tr["t"], tr["hw"]
        u = (t - (tc - hw)) / (2 * hw)
        kind = tr["kind"]
        A = lambda raw=False: self.frame_of(sa, t, raw)  # noqa: E731
        B = lambda raw=False: self.frame_of(sb, t, raw)  # noqa: E731
        floor = tr.get("color", self.cut.floor)
        if kind == "cut":
            return A() if t < tc else B()
        if kind == "dissolve":
            return E.mix(A(), B(), E.smooth(u))
        if kind == "whip":
            d = tr.get("dir", "left")
            vert = d in ("up", "down")
            px = tr.get("px", (0.6 * self.H) if vert else (0.78 * self.W))
            img = A() if u < 0.5 else B()
            return E.whip(img, img, u, d, px, tr.get("blur", 460))
        if kind == "bloom":  # symmetric: blooms up on A, back down on B
            k = math.sin(math.pi * E.clamp01(u)) * tr.get("amt", 1.0)
            return E.bloom(A() if u < 0.5 else B(), k, tr.get("color", self.cut.bloom_color))
        if kind == "bloom_in":  # bloom ramps on A, hard cut into B (optional decay on B: b_out)
            col = tr.get("color", self.cut.bloom_color)
            if u < 0.5:
                return E.bloom(A(), E.smooth(u / 0.5) * tr.get("amt", 0.9), col)
            if tr.get("b_out", 0) > 0:
                return E.bloom(B(), (1 - E.smooth((u - 0.5) / 0.5)) * tr["b_out"], col)
            return B()
        if kind == "dip":  # symmetric dip through a colour (near-black by default; "#FFFFFF" = flash)
            k = min(1.0, math.sin(math.pi * E.clamp01(u)) ** 0.6 * tr.get("amt", 1.0))
            return E.dark(A() if u < 0.5 else B(), k, floor)
        if kind == "dark_out":  # A sinks to the near-black floor, hard cut out of the dark into B
            if u < 0.5:
                return E.dark(A(), min(1.0, E.smooth(u / 0.5) ** 0.8 * tr.get("amt", 1.0)), floor)
            if tr.get("b_in", 0) > 0:
                return E.dark(B(), (1 - E.smooth((u - 0.5) / 0.5)) * tr["b_in"], floor)
            return B()
        if kind == "roll_dark":  # A barrel-rolls (accelerating, rotational blur) into the dark; B rises out of it
            if u < 0.5:
                k = u / 0.5
                sign = -1 if tr.get("dir", "ccw") == "cw" else 1
                ang = sign * tr.get("total", 160) * E.ease_in(k, 1.8)
                sc = tr.get("s0", 1.05) + tr.get("sk", 0.9) * E.ease_in(k, 1.5)
                img = E.rot_blur(A(raw=True), sign * tr.get("blur_deg", 30) * k, sc, tr.get("samples", 10),
                                 tr.get("cx"), tr.get("cy"), ang)
                return E.dark(img, E.smooth((k - 0.35) / 0.65) if k > 0.35 else 0.0, floor)
            kb = 1 - E.smooth((t - tc) / tr.get("b_dur", 0.25))
            return E.dark(B(), kb * tr.get("b_in", 0.85), floor)
        if kind == "reveal":  # B appears through an expanding feathered circle (optionally x PNG matte)
            cx, cy = tr.get("cx", self.W / 2), tr.get("cy", self.H / 2)
            far = max(math.hypot(cx - x, cy - y) for x in (0, self.W) for y in (0, self.H))
            rad = tr.get("r0", 20) + E.ease_out(u, tr.get("ease_p", 2.0)) * tr.get("r", far + tr.get("feather", 120))
            m = E.radial_mask(self.W, self.H, cx, cy, rad, tr.get("feather", 120))
            b = B()
            if tr.get("ripple"):
                b = E.ripple_edge(b, cx, cy, rad, t, tr["ripple"])
            if tr.get("matte"):
                m = m * self.matte(tr["matte"])
            m = m + (1 - m) * E.smooth((u - 0.8) / 0.2)  # always land fully on B at the end of the window
            return E.mix(A(), b, m)
        if kind == "slide_still":  # an RGBA still crosses the frame; the cut happens underneath it
            el = self.still(tr["element"], tr.get("width"))
            ew = el.shape[1] * tr.get("scale", 1.0)
            x0, x1 = tr.get("x0", self.W + ew / 2), tr.get("x1", -ew / 2)
            ue = E.EASE.get(tr.get("ease", "linear"), E.clamp01)
            x = E.lerp(x0, x1, ue(u))
            dx = abs(E.lerp(x0, x1, ue(u + 1 / (2 * hw * self.fps))) - x)
            base = A() if u < tr.get("cut_at", 0.5) else B()
            spr = el if tr.get("scale", 1.0) == 1.0 else self._scaled(tr["element"], el, tr["scale"])
            spr = E.motion_blur(spr, min(dx * tr.get("blur_k", 0.7), tr.get("max_blur", 400)))
            return E.place(base, spr, x, tr.get("y", self.H / 2))
        raise ValueError(f"unknown transition kind '{kind}'")

    _scale_cache = {}

    def _scaled(self, key, el, s):
        k = (key, round(s, 4))
        if k not in self._scale_cache:
            import cv2
            self._scale_cache.clear()
            self._scale_cache[k] = cv2.resize(el, (max(1, int(el.shape[1] * s)), max(1, int(el.shape[0] * s))),
                                              interpolation=cv2.INTER_AREA)
        return self._scale_cache[k]

    # ------------------------------------------------------------ overlays
    @staticmethod
    def fade(ov, t):
        a = float(ov.get("opacity", 1.0))
        lt, rt = t - ov["t0"], ov["t1"] - t
        if ov.get("fade_in"):
            a *= E.smooth(lt / ov["fade_in"])
        if ov.get("fade_out"):
            a *= E.smooth(rt / ov["fade_out"])
        return a

    def ov_title(self, img, ov, t):
        ts = self.cut.ts
        st = ts.style(ov.get("style", "title"), **{k: ov.get(k) for k in ("size", "wght", "color", "track")})
        lt = t - ov["t0"]
        part = ov.get("part")
        if part and lt >= part.get("at", 1e9):
            u = E.ease_in((lt - part["at"]) / part.get("dur", 0.5), 2.0)
            return ts.parting(img, ov["text"], st, u, ov.get("x"), ov.get("y"), part.get("scale", 3.0),
                              part.get("travel"), part.get("blur", 60))
        return ts.title(img, ov["text"], st, ov.get("x"), ov.get("y"), self.fade(ov, t))

    def ov_caption(self, img, ov, t):
        ts = self.cut.ts
        st = ts.style(ov.get("style", "caption"), **{k: ov.get(k) for k in ("size", "wght", "color", "track")})
        layout = ov.get("layout", "auto")
        if layout == "auto":
            layout = "stack" if self.H > self.W else "split"
        a = self.fade(ov, t)
        if layout == "split":
            return ts.split(img, ov.get("left", ""), ov.get("right", ""), st, ov.get("margin", round(self.W * 0.03125)),
                            ov.get("baseline"), a)
        lines = ov.get("lines") or [s for s in (ov.get("left"), ov.get("right")) if s]
        align = ov.get("align", "center")
        x = ov.get("x", {"left": ov.get("margin", 60), "right": self.W - ov.get("margin", 60)}.get(align, self.W / 2))
        return ts.block(img, lines, st, x, ov.get("y", self.H * 0.62), align, a)

    def ov_text(self, img, ov, t):
        ts = self.cut.ts
        st = ts.style(ov.get("style", "text"), **{k: ov.get(k) for k in ("size", "wght", "color", "track")})
        lines = ov["text"].split("\n")
        chars = None
        if ov.get("type_cps"):
            chars = int((t - ov["t0"]) * ov["type_cps"]) + 1
        align = ov.get("align", "center")
        x = ov.get("x", {"left": 60, "right": self.W - 60}.get(align, self.W / 2))
        return ts.block(img, lines, st, x, ov.get("y"), align, self.fade(ov, t), chars,
                        None if ov.get("caret", True) else False)

    def _keyed(self, ov, t, defaults):
        """keys [[t_abs, x, y, scale, angle, opacity], ...] -> dict (missing values from defaults)."""
        vals = dict(defaults)
        if ov.get("keys"):
            names = ["x", "y", "scale", "angle", "opacity"]
            got = E.interp_keys(ov["keys"], t, ov.get("keys_ease", "linear"))
            vals.update({names[i]: v for i, v in enumerate(got)})
        return vals

    def ov_image(self, img, ov, t):
        a = self.fade(ov, t)
        if ov.get("fit"):  # full-frame plate
            lay = self._fitted(ov["path"], ov["fit"], ov.get("anchor", (0.5, 0.5)))
            return E.over(img, lay, 0, 0, a)
        el = self.still(ov["path"], ov.get("width"))
        k = self._keyed(ov, t, dict(x=ov.get("x", self.W / 2), y=ov.get("y", self.H / 2), scale=ov.get("scale", 1.0),
                                    angle=ov.get("angle", 0.0), opacity=1.0))
        return E.place(img, el, k["x"], k["y"], k["scale"], k["angle"], a * k["opacity"], ov.get("blend", "normal"))

    def _fitted(self, path, fit, anchor):
        key = (path, fit, tuple(anchor), "fit")
        if key not in self.stills:
            self.stills[key] = E.fit_rgba(E.load_rgba(path), self.W, self.H, fit, anchor)
        return self.stills[key]

    def _video_src_t(self, ov, t):
        st = float(ov.get("in", 0)) + (t - ov["t0"]) * float(ov.get("speed", 1.0))
        if ov.get("loop"):
            d = E.probe(ov["path"])["duration"]
            if d > 0:
                st = st % d
        return st

    def ov_video(self, img, ov, t):
        a = self.fade(ov, t)
        src_t = self._video_src_t(ov, t)
        if ov.get("width"):
            info = E.probe(ov["path"])
            w = int(ov["width"])
            h = int(round(w * info["height"] / max(1, info["width"]) / 2)) * 2
            fr = self.clip(ov["path"], src_t, size=(w, h))
            lay = np.dstack([fr, np.ones(fr.shape[:2], np.float32)])
            k = self._keyed(ov, t, dict(x=ov.get("x", self.W / 2), y=ov.get("y", self.H / 2), scale=1.0, angle=0.0,
                                        opacity=1.0))
            return E.place(img, lay, k["x"], k["y"], k["scale"], k["angle"], a * k["opacity"], ov.get("blend", "normal"))
        fr = self.clip(ov["path"], src_t, ov.get("fit", "cover"), ov.get("anchor", (0.5, 0.5)), ov.get("bg"))
        if ov.get("blend") == "screen":
            return E.screen(img, fr, a)
        return E.mix(img, fr, a)

    def ov_card(self, img, ov, t):
        """Full-frame end card / slate: bg colour plate with an optional image or video fitted on it."""
        a = self.fade(ov, t)
        plate = np.empty((self.H, self.W, 3), np.float32)
        plate[:] = E.color(ov.get("bg", "#000000"))
        p = ov.get("path")
        if p:
            fit = ov.get("fit", "contain")
            if E.is_image(p):
                s = ov.get("scale", 1.0)
                if s == 1.0:
                    lay = self._fitted(p, fit, ov.get("anchor", (0.5, 0.5)))
                    plate = E.over(plate, lay)
                else:
                    el = self.still(p, int(self.W * s))
                    plate = E.place(plate, el, self.W / 2, self.H / 2)
            else:
                plate = self.clip(p, self._video_src_t(ov, t), fit, ov.get("anchor", (0.5, 0.5)), ov.get("bg", "#000000"))
        return plate if a >= 1 else E.mix(img, plate, a)

    def ov_ui(self, img, ov, t):
        ts = self.cut.ts
        a = self.fade(ov, t)
        fd = ov.get("fade", 0.12)
        if fd:
            a *= E.smooth((t - ov["t0"]) / fd) * E.smooth((ov["t1"] - t) / fd)
        cur_cfg = {**self.cut.ui.get("cursor", {}), **ov.get("cursor", {})}
        pill_cfg = {**self.cut.ui.get("pill", {}), **ov.get("pill", {})}
        cx = cy = None
        if ov.get("moves"):  # cursor positions [[t_abs, x, y], ...]
            cx, cy = E.interp_keys(ov["moves"], t, ov.get("moves_ease", "linear"))[:2]
        for pl in ov.get("pills", []):
            if pl["t0"] <= t < pl["t1"]:
                chars = (t - pl["t0"]) * pl.get("cps", 60)
                pa = min(1.0, (t - pl["t0"]) / 0.08) * min(1.0, (pl["t1"] - t) / 0.1)
                if "x" in pl:
                    px, py = pl["x"], pl["y"]
                else:
                    px, py = cx + pl.get("dx", 34), cy + pl.get("dy", -46)
                img = U.draw_pill(img, ts, px, py, pl["text"], chars, {**pill_cfg, **pl.get("pill", {})}, pa * a,
                                  caret=pl.get("caret", True))
        if cx is not None and not ov.get("hide_cursor"):
            img = U.draw_cursor(img, cur_cfg, cx, cy, t, ov.get("clicks", []), a, self.cut.P)
        return img

    def ov_slide_still(self, img, ov, t):
        """RGBA still crossing the frame along keys [[t_abs, cx, cy, scale], ...] with velocity motion blur."""
        el = self.still(ov["path"], ov.get("width"))
        ks = ov["keys"]
        x, y, sc = E.interp_keys(ks, t, ov.get("keys_ease", "linear"))[:3]
        x2 = E.interp_keys(ks, t + 1 / self.fps, ov.get("keys_ease", "linear"))[0]
        spr = el if abs(sc - 1) < 1e-4 else self._scaled(ov["path"], el, sc)
        spr = E.motion_blur(spr, min(abs(x2 - x) * ov.get("blur_k", 0.7), ov.get("max_blur", 400)))
        if ov.get("grade"):
            spr = np.dstack([E.grade(spr[..., :3], ov["grade"], self.cut.floor), spr[..., 3]])
        return E.place(img, spr, x, y, 1.0, 0.0, self.fade(ov, t))

    OV = {"title": ov_title, "caption": ov_caption, "text": ov_text, "image": ov_image, "video": ov_video,
          "card": ov_card, "ui": ov_ui, "slide_still": ov_slide_still}

    # ------------------------------------------------------------ frame
    def render_frame(self, n):
        t = n / self.fps
        active = [ov for ov in self.cut.ovs if ov["t0"] - 1e-9 <= t < ov["t1"] - 1e-9]
        covered = any(ov["kind"] == "card" and self.fade(ov, t) >= 1 for ov in active)
        if covered:
            img = np.zeros((self.H, self.W, 3), np.float32)
        else:
            img = None
            for tr in self.cut.trs:
                if tr["kind"] != "cut" and tr["hw"] > 0 and abs(t - tr["t"]) <= tr["hw"] + 1e-9:
                    img = self.transition(tr, t)
                    break
            if img is None:
                seg = self.seg_at(t)
                if seg is None:
                    img = np.empty((self.H, self.W, 3), np.float32)
                    img[:] = E.color(self.cut.floor)
                else:
                    img = self.frame_of(seg, t)
            fin = self.cut.finish
            if fin.get("grade"):
                img = E.grade(img, fin["grade"], self.cut.floor)
            if fin.get("halation", 0) > 0:
                img = E.halation(img, fin["halation"], fin.get("halation_threshold", 0.74), fin.get("halation_sigma", 16),
                                 tuple(fin.get("halation_tint", (1.0, 0.82, 0.62))))
            if fin.get("vignette", 0) > 0:
                img = E.vignette(img, fin["vignette"])
            if fin.get("grain", 0) > 0:
                img = E.grain(img, n, fin["grain"], fin.get("grain_seed", 0))
        for ov in active:
            fn = self.OV.get(ov["kind"])
            if fn is None:
                raise ValueError(f"unknown overlay kind '{ov['kind']}'")
            img = fn(self, img, ov, t)
        return img


# ====================================================================== validation
def check(cut, verbose=True):
    """Timeline report + warnings: gaps/overlaps, missing files, sources sampled outside the clip (freeze)."""
    warn = []
    fps = cut.fps
    files = [s["path"] for s in cut.segs] + [o["path"] for o in cut.ovs if isinstance(o.get("path"), str)]
    files += [tr[k] for tr in cut.trs for k in ("element", "matte") if tr.get(k)]
    for p in files:
        if not os.path.exists(p):
            warn.append(f"missing file: {p}")
    for a, b in zip(cut.segs, cut.segs[1:]):
        if abs(a["t1"] - b["t0"]) > 1e-6:
            warn.append(f"{'gap' if b['t0'] > a['t1'] else 'overlap'} between {a['id']} ({a['t1']:.3f}) and {b['id']} ({b['t0']:.3f})")
    for s in cut.segs:
        for k in ("t0", "t1"):
            fr = s[k] * fps
            if abs(fr - round(fr)) > 1e-3:
                warn.append(f"{s['id']}.{k}={s[k]} is not on a frame boundary at {fps:g} fps (frame {fr:.2f})")
    rows = []
    for s in cut.segs:
        lo, hi = seg_extent(cut, s)
        a, b = Renderer.src_time(s, lo), Renderer.src_time(s, hi)
        dur = None
        if os.path.exists(s["path"]) and not E.is_image(s["path"]):
            try:
                dur = E.probe(s["path"])["duration"]
            except Exception as e:  # noqa: BLE001
                warn.append(str(e))
        if dur is not None:
            last = dur - 1.0 / E.probe(s["path"])["fps"]
            if min(a, b) < -1e-6 or max(a, b) > last + 0.5 / fps:
                warn.append(f"{s['id']}: samples source {min(a, b):.3f}-{max(a, b):.3f}s but clip is {dur:.3f}s "
                            f"-> frames are held (freeze). Shorten the segment/transition or change in/speed/ramp.")
        rows.append((s["id"], s["t0"], s["t1"], a, b, dur, os.path.basename(s["path"])))
    for tr in cut.trs:
        sa, sb = cut.seg_by[tr["a"]], cut.seg_by[tr["b"]]
        if abs(tr["t"] - sb["t0"]) > 1e-6 or abs(sa["t1"] - sb["t0"]) > 1e-6:
            warn.append(f"transition {tr['a']}->{tr['b']} at {tr['t']:.3f}: segments do not abut there "
                        f"({tr['a']}.t1={sa['t1']:.3f}, {tr['b']}.t0={sb['t0']:.3f})")
    wins = sorted([(tr["t"] - tr["hw"], tr["t"] + tr["hw"], tr) for tr in cut.trs if tr["hw"] > 0 and tr["kind"] != "cut"],
                  key=lambda x: x[0])
    for (a0, a1, ta), (b0, b1, tb) in zip(wins, wins[1:]):
        if b0 < a1:
            warn.append(f"transition windows overlap: {ta['a']}->{ta['b']} and {tb['a']}->{tb['b']} (first one wins)")
    for ov in cut.ovs:
        if ov["t1"] > cut.duration + 1e-6:
            warn.append(f"overlay {ov['kind']} ends at {ov['t1']} after duration {cut.duration}")
        if ov["kind"] in ("title", "caption", "text", "ui") and not cut.ts.fonts:
            warn.append("text overlays present but no \"fonts\" configured")
    if verbose:
        print(f"{cut.W}x{cut.H} @ {cut.fps:g} fps, {cut.duration:.3f}s = {int(round(cut.duration * fps))} frames; "
              f"{len(cut.segs)} segments, {len(cut.trs)} transitions, {len(cut.ovs)} overlays")
        print(f"  {'id':10s} {'t0':>8s} {'t1':>8s}   {'src (incl. handles)':>20s}  {'clip':>7s}  file")
        for r in rows:
            d = f"{r[5]:.2f}s" if r[5] is not None else "-"
            print(f"  {r[0]:10s} {r[1]:8.3f} {r[2]:8.3f}   {r[3]:9.3f} -> {r[4]:7.3f}  {d:>7s}  {r[6]}")
        for w in warn:
            print("  WARNING:", w)
    return warn


# ====================================================================== main
def render_range(cut, out, n0, n1, a, quiet=False):
    r = Renderer(cut, a.cache_mb, (n0 / cut.fps, n1 / cut.fps))
    enc = E.Encoder(out, cut.W, cut.H, cut.fps, a.crf, a.bitrate, a.preset)
    t_start = time.time()
    step = int(round(cut.fps * 2))
    for n in range(n0, n1):
        enc.write(r.render_frame(n))
        if not quiet and (n - n0) % step == 0:
            el = time.time() - t_start
            print(f"  {n / cut.fps:7.2f}s  ({n - n0}/{n1 - n0} frames, {el:.0f}s elapsed)", flush=True)
    enc.close()
    return time.time() - t_start


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist")
    ap.add_argument("--out", default=None, help="output .mp4 (default: <cutlist dir>/out/<cutlist name>.mp4)")
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--frames", default=None, help="internal: frame range a:b (overrides --from/--to)")
    ap.add_argument("--stills", default="", help="comma-separated times (s) to dump as PNG instead of rendering")
    ap.add_argument("--stills-dir", default=None)
    ap.add_argument("--crf", type=int, default=16, help="x264 CRF (12 final, 16 default, 23 proxy)")
    ap.add_argument("--bitrate", default=None, help="e.g. 40M (overrides --crf)")
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--jobs", type=int, default=1, help="render N chunks in parallel processes, then concat")
    ap.add_argument("--cache-mb", type=int, default=4000, help="decoded-clip RAM budget per process")
    ap.add_argument("--check", action="store_true", help="validate and print the timeline only")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    E.tune_malloc()

    cut = Cut(a.cutlist)
    if a.check:
        w = check(cut)
        sys.exit(1 if any(x.startswith("missing") for x in w) else 0)
    if not a.quiet and not a.frames:
        check(cut)

    if a.stills:
        r = Renderer(cut, a.cache_mb)
        d = a.stills_dir or os.path.join(os.path.dirname(cut.path), "out", "stills")
        os.makedirs(d, exist_ok=True)
        for ts in a.stills.split(","):
            t = float(ts)
            n = int(round(t * cut.fps))
            p = os.path.join(d, f"still_{n / cut.fps:07.3f}.png")
            E.write_image(p, r.render_frame(n))
            print(p)
        return

    out = a.out or os.path.join(os.path.dirname(cut.path), "out", os.path.splitext(os.path.basename(cut.path))[0] + ".mp4")
    if a.frames:
        n0, n1 = (int(x) for x in a.frames.split(":"))
    else:
        n0 = int(round(a.t0 * cut.fps))
        n1 = int(round((a.t1 if a.t1 is not None else cut.duration) * cut.fps))
    if n1 <= n0:
        sys.exit("nothing to render (empty range)")
    t_start = time.time()
    if a.jobs > 1 and n1 - n0 >= 2 * a.jobs:
        tmp = tempfile.mkdtemp(prefix="render_parts_", dir=os.path.dirname(os.path.abspath(out)) or ".")
        bounds = [n0 + (n1 - n0) * k // a.jobs for k in range(a.jobs + 1)]
        procs, parts = [], []
        for k in range(a.jobs):
            part = os.path.join(tmp, f"part_{k:03d}.mp4")
            parts.append(part)
            cmd = [sys.executable, os.path.abspath(__file__), cut.path, "--frames", f"{bounds[k]}:{bounds[k + 1]}",
                   "--out", part, "--crf", str(a.crf), "--preset", a.preset, "--cache-mb", str(max(800, a.cache_mb // a.jobs)),
                   "--quiet"] + (["--bitrate", a.bitrate] if a.bitrate else [])
            procs.append(subprocess.Popen(cmd))
        print(f"rendering frames {n0}-{n1} in {a.jobs} parallel chunks ...", flush=True)
        if any(p.wait() != 0 for p in procs):
            sys.exit("a render chunk failed")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", "-movflags", "+faststart", out], check=True)
        for p in parts + [lst]:
            os.remove(p)
        os.rmdir(tmp)
    else:
        render_range(cut, out, n0, n1, a, a.quiet)
    wall = time.time() - t_start
    secs = (n1 - n0) / cut.fps
    if not a.quiet or not a.frames:
        print(f"wrote {out}: {n1 - n0} frames ({secs:.2f}s) in {wall:.1f}s wall = {wall / secs:.2f}s per output second")


if __name__ == "__main__":
    main()
