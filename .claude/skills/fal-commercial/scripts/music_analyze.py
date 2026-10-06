#!/usr/bin/env python3
"""OPTIONAL (needs librosa): objective structure checks for music takes against your cut.

  python music_analyze.py music/take_*.mp3 --grid 0.5 --hits 6,12,18,26,30 \\
      --sections "0-6:2,6-12:4,12-18:8,18-26:6" [--ref refs/reference_audio.wav] [--json music/analysis.json]

Per take:
  tempo / n_beats          librosa beat tracker
  grid_coh, grid_phase     how tightly the beats sit on your cut grid (--grid seconds, e.g. 0.5 s = 120 BPM =
                           12 frames at 24 fps). coh 1.0 = every beat on the grid. phase = offset of the beats from
                           the grid; advance_s = how much to trim off the head of the music (mix.json music.trim_start)
                           so beats land on exact grid lines (the reference run advanced its score by 0.1 s).
  hits                     onset strength (normalised to the take's 95th percentile) within +/-0.12 s of each cut/hit
                           time you need the music to support; ~1.0+ = a clear accent, < 0.3 = nothing there.
  sect_db, corr_energy     mean RMS dB per section and its correlation with the energy curve you asked for
                           (sections "start-end:expected_energy,...", any scale).
  ref_chroma_maxsim        (with --ref) crude max cosine similarity of 4 s chroma windows vs a reference. Only a
                           screen; use cover_detect.py (calibrated with controls) for the originality decision.
"""
import argparse
import json
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
try:
    import librosa
except ImportError:  # pragma: no cover
    sys.exit("music_analyze.py needs librosa: pip install librosa soundfile")

SR = 22050


def windows(C, w=172, s=43):
    return np.array([C[:, i:i + w].flatten() for i in range(0, max(1, C.shape[1] - w), s)])


def norm_rows(A):
    A = A - A.mean(1, keepdims=True)
    return A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)


def analyze(path, grid, hits, sections, ref_chroma=None):
    y, sr = librosa.load(path, sr=SR, mono=True)
    dur = len(y) / sr
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    beats = np.asarray(beats, float)
    g = grid or (60.0 / tempo if tempo > 0 else 0.5)
    if len(beats):
        z = np.mean(np.exp(2j * np.pi * beats / g))
        coh = float(abs(z))
        phase = float(np.angle(z) / (2 * np.pi) * g)
    else:
        coh, phase = 0.0, 0.0
    advance = phase % g  # trimming this much off the head moves the beats onto k*grid
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    t = librosa.times_like(rms, sr=sr, hop_length=512)
    rmsdb = 20 * np.log10(rms + 1e-6)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
    ot = librosa.times_like(onset, sr=sr, hop_length=512)
    on_n = onset / (np.percentile(onset, 95) + 1e-6)
    hit_vals = {}
    for h in hits:
        m = (ot > h - 0.12) & (ot < h + 0.12)
        hit_vals[str(h)] = round(float(on_n[m].max()) if m.any() else 0.0, 2)
    sect, corr = [], None
    for a, b, _ in sections:
        m = (t >= a) & (t < b)
        sect.append(round(float(np.mean(rmsdb[m])) if m.any() else -99.0, 1))
    if len(sections) >= 3:
        exp, meas = np.array([e for _, _, e in sections], float), np.array(sect, float)
        if exp.std() > 0 and meas.std() > 0:
            corr = round(float(np.corrcoef(exp, meas)[0, 1]), 2)
    orig = None
    if ref_chroma is not None:
        c = librosa.feature.chroma_cqt(y=y, sr=sr)
        A, B = norm_rows(windows(c)), norm_rows(windows(ref_chroma))
        orig = round(float((A @ B.T).max()), 3)
    return dict(dur=round(dur, 2), tempo=round(tempo, 1), n_beats=int(len(beats)), grid=round(g, 4),
                grid_coh=round(coh, 2), grid_phase=round(phase, 3), advance_s=round(advance, 3),
                corr_energy=corr, sect_db=sect, hits=hit_vals,
                hitmean=round(float(np.mean(list(hit_vals.values()))), 2) if hit_vals else None,
                ref_chroma_maxsim=orig, beats=[round(float(b), 3) for b in beats])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--grid", type=float, default=None, help="cut grid in seconds (default: the take's beat period)")
    ap.add_argument("--hits", default="", help="comma-separated times (s) that need musical accents")
    ap.add_argument("--sections", default="", help='"start-end:energy,..." expected energy curve')
    ap.add_argument("--ref", default=None, help="reference audio for a crude chroma similarity screen")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    hits = [float(x) for x in a.hits.split(",") if x.strip()]
    sections = []
    for part in [p for p in a.sections.split(",") if p.strip()]:
        rng, e = part.split(":")
        s0, s1 = rng.split("-")
        sections.append((float(s0), float(s1), float(e)))
    ref_chroma = None
    if a.ref:
        ry, _ = librosa.load(a.ref, sr=SR, mono=True)
        ref_chroma = librosa.feature.chroma_cqt(y=ry, sr=SR)
    out = {}
    for f in a.files:
        r = analyze(f, a.grid, hits, sections, ref_chroma)
        out[f] = r
        short = {k: v for k, v in r.items() if k != "beats"}
        print(f, json.dumps(short))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=1)
        print("wrote", a.json)


if __name__ == "__main__":
    main()
