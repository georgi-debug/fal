#!/usr/bin/env python3
"""Fast self-tests for the fal-media helper scripts (stdlib unittest, synthetic data only).

    python3 -m unittest discover -s .claude/skills/fal-media/scripts/tests -v
    python3 .claude/skills/fal-media/scripts/tests/test_scripts.py

Ledger and falgen tests need only the standard library. Media tests are skipped when
ffmpeg/ffprobe, Pillow, numpy or OpenCV are missing. No network, no fal key, no cost.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__
sys.path.insert(0, SCRIPTS)
import ledger  # noqa: E402


def have(*mods):
    for m in mods:
        try:
            importlib.import_module(m)
        except ImportError:
            return False
    return True


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAVE_IMG = have("PIL", "numpy", "cv2")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="falmedia_test_")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("FAL_BUDGET_CAP", "FAL_GROUP_CAPS", "FAL_PRICES", "FAL_KEY",
                                 "FAL_MCP_KEY")}
        self.env["FAL_LEDGER"] = os.path.join(self.tmp, "ledger.jsonl")
        self.env["FAL_UPLOAD_CACHE"] = os.path.join(self.tmp, "uploads.json")
        self._patch = mock.patch.dict(os.environ, self.env, clear=True)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, script, *args, env=None):
        e = dict(self.env, **(env or {}))
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + [str(a) for a in args],
                           capture_output=True, text=True, env=e, cwd=self.tmp)
        return p.returncode, p.stdout + p.stderr


class TestEstimates(Base):
    def test_seedance(self):
        ep = "bytedance/seedance-2.5/image-to-video"
        self.assertAlmostEqual(ledger.estimate(ep, duration=5, draft=True), 1.028, places=3)
        self.assertAlmostEqual(ledger.estimate(ep, duration=5, resolution="1080p", draft=True),
                               1.028, places=3)  # draft forces 480p
        self.assertAlmostEqual(ledger.estimate("bytedance/seedance-2.5/draft/complete", duration=5),
                               5.686, places=3)
        self.assertAlmostEqual(ledger.estimate(ep, duration=1, draft=True), 0.206, places=3)
        self.assertAlmostEqual(ledger.estimate("bytedance/seedance-2.5/draft/complete", duration=1),
                               1.137, places=3)
        us = ledger.estimate("bytedance/seedance-2.5/us/image-to-video", duration=5, resolution="1080p")
        self.assertAlmostEqual(us / 5.686, 1.2, places=2)

    def test_fixed_prices(self):
        self.assertAlmostEqual(ledger.estimate("fal-ai/nano-banana-pro/edit", num_images=2), 0.30)
        self.assertAlmostEqual(ledger.estimate("fal-ai/nano-banana-pro", resolution="4K"), 0.30)
        self.assertAlmostEqual(ledger.estimate("google/lyria-3.5"), 0.10)
        self.assertAlmostEqual(ledger.estimate("elevenlabs/music/v2.5", music_seconds=120), 1.20)
        self.assertAlmostEqual(ledger.estimate("elevenlabs/music/v2.5", music_seconds=20), 0.60)
        self.assertAlmostEqual(ledger.estimate("fal-ai/elevenlabs/sound-effects/v2", sfx_seconds=5), 0.01)
        self.assertAlmostEqual(ledger.estimate("openrouter/router/video"), 0.30)

    def test_from_args(self):
        self.assertAlmostEqual(ledger.estimate_from_args(
            "bytedance/seedance-2.5/image-to-video",
            {"duration": "5", "resolution": "480p", "draft": True}), 1.028, places=3)
        self.assertAlmostEqual(ledger.estimate_from_args(
            "bytedance/seedance-2.5/draft/complete", {"_duration": 5}), 5.686, places=3)

    def test_unknown_and_override(self):
        rc, out = self.cli("ledger.py", "estimate", "acme/new-model")
        self.assertEqual(rc, 3)
        self.assertIn("get_pricing", out)
        prices = os.path.join(self.tmp, "prices.json")
        with open(prices, "w") as f:
            json.dump({"acme/new-model": 0.42, "google/lyria-3.5": {"unit": "per_call", "price": 0.2}}, f)
        rc, out = self.cli("ledger.py", "estimate", "acme/new-model", env={"FAL_PRICES": prices})
        self.assertEqual((rc, out.strip()), (0, "0.4200"))
        rc, out = self.cli("ledger.py", "estimate", "google/lyria-3.5", env={"FAL_PRICES": prices})
        self.assertEqual(out.strip(), "0.2000")


class TestLedger(Base):
    def test_cap_required(self):
        rc, out = self.cli("ledger.py", "reserve", "--tag", "G1_a", "--endpoint", "x", "--est", "1")
        self.assertEqual(rc, 1)
        self.assertIn("FAL_BUDGET_CAP", out)
        self.assertFalse(os.path.exists(self.env["FAL_LEDGER"]))

    def test_reserve_settle_report(self):
        env = {"FAL_BUDGET_CAP": "10", "FAL_GROUP_CAPS": "G1:3"}
        ep = "bytedance/seedance-2.5/image-to-video"
        r = lambda tag, *a: self.cli("ledger.py", "reserve", "--tag", tag, "--endpoint", ep, *a, env=env)
        self.assertEqual(r("G1_a_d1", "--duration", "5", "--draft")[0], 0)
        self.assertEqual(r("G1_a_d1", "--duration", "5", "--draft")[0], 4)       # duplicate tag
        self.assertEqual(r("G1_a_d2", "--duration", "5", "--draft")[0], 0)
        rc, out = r("G1_a_d3", "--duration", "5", "--draft")                    # 3.08 > 3
        self.assertEqual(rc, 2)
        self.assertIn("GROUP CAP", out)
        self.assertEqual(r("G2_b", "--est", "7")[0], 0)                          # 9.06 total
        rc, out = r("G3_c", "--est", "1")                                       # 10.06 > 10
        self.assertEqual(rc, 2)
        self.assertIn("BUDGET CAP", out)
        self.assertEqual(r("G3_c", "--est", "0.5", "--dry-run")[0], 0)
        n_rows = len(ledger.read_rows())
        self.assertEqual(n_rows, 3)                                             # refusals wrote nothing
        s = lambda tag, st, *a: self.cli("ledger.py", "settle", "--tag", tag, "--status", st, *a, env=env)
        self.assertEqual(self.cli("ledger.py", "rid", "--tag", "G1_a_d1", "--request-id", "r1")[0], 0)
        self.assertEqual(s("G1_a_d1", "ok")[0], 0)
        self.assertEqual(s("G1_a_d1", "refunded")[0], 4)                         # already settled
        self.assertEqual(s("G1_a_d2", "refunded", "--note", "likeness filter")[0], 0)
        self.assertEqual(s("nope", "ok")[0], 5)
        self.assertEqual(r("G1_a_d3", "--duration", "5", "--draft")[0], 0)      # fits after refund
        rc, out = self.cli("ledger.py", "report", "--by", "status", "--json", env=env)
        self.assertEqual(rc, 0)
        rep = json.loads(out)
        self.assertAlmostEqual(rep["spent"], 1.028 * 2 + 7, places=3)
        self.assertAlmostEqual(rep["remaining"], 10 - (1.028 * 2 + 7), places=3)
        self.assertEqual(rep["counts"], {"ok": 1, "refunded": 1, "failed": 0, "charged": 0, "pending": 2})
        self.assertEqual(rep["pending_tags"], ["G1_a_d3", "G2_b"])
        self.assertAlmostEqual(rep["group_caps"]["G1"]["spent"], 2.056, places=3)
        self.assertEqual(rep["breakdown"]["refunded"]["spent"], 0)
        self.assertEqual(rep["breakdown"]["ok"]["spent"], 1.028)
        jobs = ledger.jobs(ledger.read_rows())
        self.assertEqual(jobs["G1_a_d1"]["request_id"], "r1")
        for by in ("endpoint", "group"):
            self.assertEqual(self.cli("ledger.py", "report", "--by", by, env=env)[0], 0)

    def test_explicit_group(self):
        env = {"FAL_BUDGET_CAP": "100", "FAL_GROUP_CAPS": "music:0.25"}
        a = ("reserve", "--endpoint", "google/lyria-3.5", "--group", "music")
        self.assertEqual(self.cli("ledger.py", *a, "--tag", "take1", env=env)[0], 0)
        self.assertEqual(self.cli("ledger.py", *a, "--tag", "take2", env=env)[0], 0)
        self.assertEqual(self.cli("ledger.py", *a, "--tag", "take3", env=env)[0], 2)

    def test_concurrent_reserves_never_exceed_cap(self):
        env = dict(self.env, FAL_BUDGET_CAP="7.5")
        procs = [subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "ledger.py"), "reserve",
                                   "--tag", f"P{i}_job", "--endpoint", "x", "--est", "1"],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for i in range(20)]
        codes = [p.wait() for p in procs]
        self.assertEqual(sorted(codes), [0] * 7 + [2] * 13)
        rows = ledger.read_rows()
        self.assertEqual(len(rows), 7)
        self.assertLessEqual(ledger.spent(rows), 7.5)


class TestFalgen(Base):
    def write_jobs(self, jobs):
        p = os.path.join(self.tmp, "jobs.json")
        with open(p, "w") as f:
            json.dump(jobs, f)
        return p

    def test_help_and_dry_run(self):
        rc, out = self.cli("falgen.py", "--help")
        self.assertEqual(rc, 0)
        kf = os.path.join(self.tmp, "kf.png")
        write(kf, "x")
        jobs = self.write_jobs([
            {"tag": "G1_a_d1", "endpoint": "bytedance/seedance-2.5/image-to-video",
             "args": {"image_url": f"file://{kf}", "duration": "5", "resolution": "480p", "draft": True}},
            {"tag": "G1_a_d2", "endpoint": "acme/unknown", "args": {}},
            {"tag": "G1_a_d3", "endpoint": "acme/unknown", "args": {}, "est": 0.5},
            {"tag": "G1_big", "endpoint": "bytedance/seedance-2.5/draft/complete", "args": {"_duration": 5}},
        ])
        rc, out = self.cli("falgen.py", jobs, "--dry-run", env={"FAL_BUDGET_CAP": "5"})
        self.assertEqual(rc, 2, out)
        self.assertIn("RESERVE  G1_a_d1", out)
        self.assertIn("UNKNOWN  G1_a_d2", out)
        self.assertIn("RESERVE  G1_a_d3", out)
        self.assertIn("REFUSE   G1_big", out)
        self.assertFalse(os.path.exists(self.env["FAL_LEDGER"]))  # dry run writes nothing

    def test_run_with_fake_client(self):
        import falgen

        class Completed:
            pass

        class HTTPError(Exception):
            pass

        class Handle:
            def __init__(self, ok):
                self.ok, self.request_id = ok, f"rid-{id(self)}"

            def iter_events(self, interval=0.1):
                yield Completed()

            def get(self):
                if not self.ok:
                    raise HTTPError("content_policy_violation")
                return {"video": {"url": "https://example.invalid/out.mp4"}}

        class Fake:
            pass

        fake = Fake()
        fake.Completed, fake.FalClientHTTPError = Completed, HTTPError
        fake.submit = lambda ep, arguments: Handle(ok="blocked" not in arguments.get("prompt", ""))
        fake.upload_file = lambda p: "https://example.invalid/up.png"
        os.environ["FAL_BUDGET_CAP"] = "20"
        out_root = os.path.join(self.tmp, "gens")
        kf = os.path.join(self.tmp, "kf.png")
        write(kf, "img")
        with mock.patch.object(falgen, "_fal", fake), \
                mock.patch.object(falgen, "download", lambda url, dest: write(dest, "") or dest):
            res = falgen.run_jobs([
                {"tag": "G1_ok", "endpoint": "google/lyria-3.5", "args": {"prompt": "fine", "img": f"file://{kf}"}},
                {"tag": "G1_blk", "endpoint": "google/lyria-3.5", "args": {"prompt": "blocked"}},
            ], workers=2, out_root=out_root)
            self.assertNotIn("error", res["G1_ok"])
            self.assertEqual(res["G1_blk"]["kind"], "failed")
            again = falgen.run_jobs([{"tag": "G1_ok", "endpoint": "google/lyria-3.5", "args": {}}],
                                    out_root=out_root)
            self.assertNotIn("error", again["G1_ok"])  # skipped via result.json
        jobs = ledger.jobs(ledger.read_rows())
        self.assertEqual(jobs["G1_ok"]["outcome"], "ok")
        self.assertEqual(jobs["G1_blk"]["outcome"], "failed")
        self.assertAlmostEqual(ledger.spent(ledger.read_rows()), 0.10)
        req = json.loads(read_bytes(os.path.join(out_root, "G1_ok", "request.json")))
        self.assertEqual(req["args"]["img"], "https://example.invalid/up.png")


@unittest.skipUnless(HAVE_FFMPEG and HAVE_IMG, "needs ffmpeg/ffprobe, Pillow, numpy, OpenCV")
class TestMedia(Base):
    def setUp(self):
        super().setUp()
        self.video = os.path.join(self.tmp, "clip.mp4")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=24:duration=2", "-pix_fmt", "yuv420p",
                        self.video], check=True)
        self.image = os.path.join(self.tmp, "kf.png")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=1:duration=1", "-frames:v", "1", self.image],
                       check=True)

    def size(self, path):
        from PIL import Image
        with Image.open(path) as im:
            return im.size

    def test_contact_sheet(self):
        out = os.path.join(self.tmp, "sheet.jpg")
        rc, log = self.cli("contact_sheet.py", self.video, out, "--fps", "4", "--cols", "4", "--width", "80")
        self.assertEqual(rc, 0, log)
        self.assertIn("8 frames", log)
        segs = os.path.join(self.tmp, "segs.json")
        write(segs, json.dumps([{"t0": 0, "t1": 1, "label": "A"}, {"start": 1, "end": 2, "id": "B"}]))
        out2 = os.path.join(self.tmp, "sheet.png")
        rc, log = self.cli("contact_sheet.py", self.video, out2, "--every", "0.5", "--start", "0.5",
                           "--end", "1.5", "--segments", segs)
        self.assertEqual(rc, 0, log)
        self.assertIn("3 frames", log)
        self.assertEqual(self.cli("contact_sheet.py", self.video, out, "--fps", "500")[0], 1)
        self.assertEqual(self.cli("contact_sheet.py", "missing.mp4", out)[0], 1)

    def test_extract_frames(self):
        for mode in (["--first"], ["--last"], ["--at", "1.0"]):
            out = os.path.join(self.tmp, f"f{mode[0]}.png")
            rc, log = self.cli("extract_frames.py", self.video, out, *mode)
            self.assertEqual(rc, 0, log)
            self.assertEqual(self.size(out), (320, 180))
        self.assertEqual(self.cli("extract_frames.py", self.video, "x.png", "--at", "5")[0], 1)
        # last frame equals the final decoded frame
        import cv2
        import numpy as np
        cap, last = cv2.VideoCapture(self.video), None
        while True:
            ok, f = cap.read()
            if not ok:
                break
            last = f
        got = cv2.imread(os.path.join(self.tmp, "f--last.png"))
        self.assertLess(float(np.abs(got.astype(int) - last.astype(int)).mean()), 1.0)

    def test_soften_face(self):
        cases = {"close": (["--preset", "close"], (1920, 1080)),
                 "profile": (["--preset", "profile"], (1280, 720)),
                 "bloom": (["--preset", "bloom", "--strength", "0.7", "--center", "0.5,0.4"], (640, 360)),
                 "box": (["--box", "200,80,120,160", "--blur", "2.0"], (640, 360))}
        for name, (args, size) in cases.items():
            out = os.path.join(self.tmp, f"{name}.png")
            rc, log = self.cli("soften_face.py", self.image, out, *args)
            self.assertEqual(rc, 0, log)
            self.assertEqual(self.size(out), size, name)
        a, b = os.path.join(self.tmp, "a.png"), os.path.join(self.tmp, "b.png")
        self.cli("soften_face.py", self.image, a, "--preset", "close")
        self.cli("soften_face.py", self.image, b, "--preset", "close")
        self.assertEqual(read_bytes(a), read_bytes(b))  # deterministic grain
        self.assertEqual(self.cli("soften_face.py", self.image, a)[0], 1)  # nothing to do


if __name__ == "__main__":
    unittest.main()
