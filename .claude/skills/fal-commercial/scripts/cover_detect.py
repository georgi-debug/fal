#!/usr/bin/env python3
"""OPTIONAL (needs librosa): originality check of music takes against a reference song (cover-song detection).

  python cover_detect.py --ref refs/reference_audio.wav music/take_a.mp3 music/take_b.mp3 \\
      --neg music/unrelated_1.mp3 music/unrelated_2.mp3 [--json music/cover_detect.json]

Method: beat-synchronous chroma (median per beat) stacked with 3 beats of context, cross-similarity against the
reference for all 12 key rotations, then the longest recurrence-quantification path (librosa rqa) = Qmax. It is
key-invariant (rotations) and tempo-tolerant (beat sync), so a transposed or re-tempo'd copy of the melody or
chord sequence still scores high.

CALIBRATE EVERY TIME — Qmax has no universal threshold; it depends on the reference, genre and lengths:
  * positive controls are built automatically from the reference itself (pitch +2 semitones and 8 % faster;
    a 30 s excerpt). A real copy should score near them.
  * negative controls (--neg) must be music you know is unrelated, ideally the same genre/instrumentation as your
    takes. Unrelated music in the same style can still score well above zero.
  * judge each take by where it falls between the two groups. With no --neg the verdict is "uncalibrated".
(An AI listener's "sounds like song X" judgement flagged unrelated tracks on the reference run; this replaced it.)
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
    sys.exit("cover_detect.py needs librosa: pip install librosa soundfile")

SR = 22050


def feats(y):
    _, beats = librosa.beat.beat_track(y=y, sr=SR)
    C = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=512)
    Cs = librosa.util.sync(C, beats, aggregate=np.median)
    return librosa.feature.stack_memory(Cs, n_steps=3, delay=1)


def qmax(A, B):
    """Max RQA path length between reference features A and candidate B over the 12 chroma rotations."""
    best = 0.0
    k = max(2, int(0.1 * min(A.shape[1], B.shape[1])))
    for r in range(12):
        Bk = np.vstack([np.roll(B[i * 12:(i + 1) * 12], r, axis=0) for i in range(B.shape[0] // 12)])
        S = librosa.segment.cross_similarity(Bk, A, metric="cosine", mode="affinity", k=k)
        L, _ = librosa.sequence.rqa(S, gap_onset=5, gap_extend=1, knight_moves=True, backtrack=True)
        best = max(best, float(L.max()))
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="candidate takes")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--neg", nargs="*", default=[], help="known-unrelated tracks (negative controls)")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds of each file to analyse")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    ref, _ = librosa.load(a.ref, sr=SR, duration=a.duration)
    Fr = feats(ref)
    pos = {"POS_ref_pitch+2_rate1.08": librosa.effects.time_stretch(
        librosa.effects.pitch_shift(ref, sr=SR, n_steps=2), rate=1.08)}
    n = len(ref)
    seg = ref[int(n * 0.25): int(n * 0.25) + 30 * SR] if n > 40 * SR else ref[n // 4: 3 * n // 4]
    pos["POS_ref_excerpt"] = seg
    res = {"positive": {}, "negative": {}, "candidates": {}}
    for k, y in pos.items():
        res["positive"][k] = qmax(Fr, feats(y))
    for f in a.neg:
        y, _ = librosa.load(f, sr=SR, duration=a.duration)
        res["negative"][f] = qmax(Fr, feats(y))
    for f in a.files:
        y, _ = librosa.load(f, sr=SR, duration=a.duration)
        res["candidates"][f] = qmax(Fr, feats(y))

    pmin = min(res["positive"].values())
    nmax = max(res["negative"].values()) if res["negative"] else None
    mid = (pmin + nmax) / 2 if nmax is not None else None
    for grp in ("positive", "negative", "candidates"):
        for k, q in res[grp].items():
            tag = ""
            if grp == "candidates":
                if mid is None:
                    tag = "  (uncalibrated: add --neg controls)"
                elif nmax >= pmin:
                    tag = "  (controls overlap: detector cannot separate them for this reference)"
                else:
                    tag = "  FLAG: closer to the positive controls" if q >= mid else "  ok: within the negative range" \
                        if q <= nmax else "  borderline: between controls, listen"
            print(f"{grp[:3].upper():4s} {k:46s} Qmax={q:7.1f}{tag}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1)
        print("wrote", a.json)


if __name__ == "__main__":
    main()
