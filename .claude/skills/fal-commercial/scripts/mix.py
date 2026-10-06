#!/usr/bin/env python3
"""JSON-driven music + SFX mix, two-pass loudness master, and mux.

  python mix.py mix  comp/mix.json --out comp/out/mix_v1.wav      # premix -> loudnorm pass 1 (measure) -> pass 2
  python mix.py measure comp/out/mix_v1.wav                        # integrated LUFS, LRA, true peak (ebur128)
  python mix.py mux --video comp/out/cut_v1.mp4 --audio comp/out/mix_v1.wav --out deliverables/spot_v1.mp4

Every ffmpeg call is an argument list (no shell), so no quoting bugs (the original pipeline lost time to
zsh reading `$M:linear` as a variable modifier and word-splitting a loop).

Mix chain (format: ../reference/cutlist-format.md):
  * music: trim_start, gain, optional loop, fade in/out (timeline seconds, ffmpeg afade curve), placed at `at`.
  * each SFX: peak-normalised to -3 dBFS first (boost capped at +24 dB: generated SFX levels differ by up to
    ~48 dB), then cue gain_db + sfx_bus_db; optional duration / fade_in / fade_out / loop / trim_start.
  * amix normalize=0 (plain sum), padded/trimmed to `duration`, written as 32-bit float (no clipping).
  * master: loudnorm I=-14 TP=-1.5 LRA=11, pass 1 measures (print_format=json), pass 2 applies the measured
    values with linear=true, then resamples to 48 kHz (loudnorm works at 192 kHz internally).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

FF = ["ffmpeg", "-hide_banner", "-nostdin"]


def run(cmd, capture=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed ({r.returncode}):\n  {' '.join(cmd)}\n{r.stderr[-3000:]}")
    return r.stderr if capture else None


def duration_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        sys.exit(f"cannot read duration of {path}: {r.stderr.strip()}")


_PEAK = {}


def peak_db(path):
    if path not in _PEAK:
        err = run(FF + ["-i", path, "-af", "volumedetect", "-f", "null", "-"], capture=True)
        m = re.search(r"max_volume: ([-0-9.]+) dB", err)
        _PEAK[path] = float(m.group(1)) if m else 0.0
    return _PEAK[path]


def f6(x):
    return f"{float(x):.6f}"


def track_chain(idx, item, sr, total, is_sfx, norm_cfg, bus_db):
    """Filter chain for one input -> label [a{idx}]. Times in the item: start/at = timeline seconds."""
    at = float(item.get("start", item.get("at", 0.0)))
    chain = [f"[{idx}:a]aresample={sr}", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    ts = float(item.get("trim_start", 0.0))
    dur = item.get("duration")
    if dur is None and item.get("loop"):
        dur = max(0.0, total - at)
    if ts > 0 or dur is not None:
        chain.append(f"atrim=start={f6(ts)}" + (f":duration={f6(dur)}" if dur is not None else ""))
        chain.append("asetpts=PTS-STARTPTS")
    gain = float(item.get("gain_db", 0.0))
    if is_sfx:
        if norm_cfg:
            target = float(norm_cfg.get("peak_db", -3.0))
            cap = float(norm_cfg.get("max_boost_db", 24.0))
            gain += min(cap, target - peak_db(item["file"]))
        gain += bus_db
    chain.append(f"volume={f6(gain)}dB")
    if item.get("fade_in"):
        fi = item["fade_in"]
        fi = fi if isinstance(fi, dict) else {"duration": fi}
        chain.append(f"afade=t=in:st=0:d={f6(fi['duration'])}:curve={fi.get('curve', 'tri')}")
    fo = item.get("fade_out")
    if fo:
        fo = fo if isinstance(fo, dict) else {"duration": fo}
        if "start" in fo:  # timeline seconds
            st = float(fo["start"]) - at
        else:  # fade at the end of the (trimmed) item
            length = dur if dur is not None else duration_of(item["file"]) - ts
            st = max(0.0, length - float(fo["duration"]))
        chain.append(f"afade=t=out:st={f6(max(0.0, st))}:d={f6(fo['duration'])}:curve={fo.get('curve', 'tri')}")
    d = int(round(at * sr))
    if d > 0:
        chain.append(f"adelay=delays={d}S:all=1")
    return ",".join(chain) + f"[a{idx}]"


def premix(cfg, base, out_path):
    sr = int(cfg.get("sample_rate", 48000))
    total = float(cfg["duration"])
    music = cfg.get("music") or []
    music = [music] if isinstance(music, dict) else music
    sfx = cfg.get("sfx", [])
    norm = cfg.get("sfx_normalize", {"peak_db": -3.0, "max_boost_db": 24.0})
    bus = float(cfg.get("sfx_bus_db", 0.0))
    inputs, chains, labels = [], [], []
    for i, item in enumerate(music + sfx):
        item = dict(item)
        p = item["file"] if os.path.isabs(item["file"]) else os.path.join(base, item["file"])
        if not os.path.exists(p):
            sys.exit(f"missing audio file: {p}")
        item["file"] = p
        if item.get("loop"):
            inputs += ["-stream_loop", "-1"]
        inputs += ["-i", p]
        chains.append(track_chain(i, item, sr, total, i >= len(music), norm, bus))
        labels.append(f"[a{i}]")
    if not labels:
        sys.exit("mix has no music and no sfx")
    chains.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0,"
                  f"apad=whole_dur={f6(total)},atrim=0:{f6(total)}[mix]")
    graph = ";".join(chains)
    run(FF + ["-loglevel", "error", "-y"] + inputs + ["-filter_complex", graph, "-map", "[mix]", "-ar", str(sr),
                                                       "-c:a", "pcm_f32le", out_path])
    return graph


def loudnorm_json(stderr):
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not blocks:
        sys.exit("loudnorm produced no JSON:\n" + stderr[-2000:])
    return json.loads(blocks[-1])


def master(src, out, cfg):
    m = cfg.get("master", {})
    I, TP, LRA = m.get("I", -14.0), m.get("TP", -1.5), m.get("LRA", 11.0)
    sr = int(cfg.get("sample_rate", 48000))
    codec = m.get("codec", "pcm_s24le")
    ln = f"loudnorm=I={I}:TP={TP}:LRA={LRA}"
    p1 = loudnorm_json(run(FF + ["-i", src, "-af", ln + ":print_format=json", "-f", "null", "-"], capture=True))
    print(f"pass 1 measured: I={p1['input_i']} LUFS  TP={p1['input_tp']} dBTP  LRA={p1['input_lra']} LU  "
          f"thresh={p1['input_thresh']}  offset={p1['target_offset']}")
    if not m.get("two_pass", True):
        run(FF + ["-loglevel", "error", "-y", "-i", src, "-af", f"{ln},aresample={sr}", "-ar", str(sr), "-c:a", codec, out])
        return p1, None
    ln2 = (f"{ln}:measured_I={p1['input_i']}:measured_TP={p1['input_tp']}:measured_LRA={p1['input_lra']}"
           f":measured_thresh={p1['input_thresh']}:offset={p1['target_offset']}:linear=true:print_format=json")
    p2 = loudnorm_json(run(FF + ["-y", "-i", src, "-af", f"{ln2},aresample={sr}", "-ar", str(sr), "-c:a", codec, out],
                           capture=True))
    print(f"pass 2: normalization_type={p2.get('normalization_type')} (dynamic = the TP ceiling forced loudnorm's "
          f"compressor; lower the loud peaks or relax TP if that matters)")
    return p1, p2


def measure(path):
    err = run(FF + ["-nostats", "-i", path, "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], capture=True)
    summ = err[err.rfind("Summary:"):]

    def g(pat):
        mm = re.search(pat, summ)
        return float(mm.group(1)) if mm else None

    res = dict(I=g(r"I:\s+([-0-9.]+) LUFS"), LRA=g(r"LRA:\s+([-0-9.]+) LU"), TP=g(r"Peak:\s+([-0-9.]+) dBFS"),
               duration=duration_of(path))
    ln = loudnorm_json(run(FF + ["-nostats", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                           capture=True))
    res["loudnorm_I"], res["loudnorm_TP"] = float(ln["input_i"]), float(ln["input_tp"])
    print(f"{path}: {res['duration']:.3f}s\n"
          f"  ebur128 : integrated {res['I']} LUFS, LRA {res['LRA']} LU, true peak {res['TP']} dBTP\n"
          f"  loudnorm: integrated {res['loudnorm_I']} LUFS, true peak {res['loudnorm_TP']} dBTP "
          f"(the two meters can differ by a few tenths on short or synthetic material)")
    return res


def mux(video, audio, out, bitrate="320k", sr=48000):
    vd, ad = duration_of(video), duration_of(audio)
    if abs(vd - ad) > 0.05:
        print(f"WARNING: video {vd:.3f}s vs audio {ad:.3f}s differ; the mix 'duration' should equal the cut's")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    run(FF + ["-loglevel", "error", "-y", "-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
              "-c:v", "copy", "-c:a", "aac", "-b:a", bitrate, "-ar", str(sr), "-movflags", "+faststart", out])
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("mix")
    a1.add_argument("config")
    a1.add_argument("--out", required=True)
    a1.add_argument("--keep-premix", action="store_true", help="also keep the un-normalised float premix")
    a1.add_argument("--print-graph", action="store_true")
    a2 = sub.add_parser("measure")
    a2.add_argument("file")
    a3 = sub.add_parser("mux")
    a3.add_argument("--video", required=True)
    a3.add_argument("--audio", required=True)
    a3.add_argument("--out", required=True)
    a3.add_argument("--bitrate", default="320k")
    a = ap.parse_args()

    if a.cmd == "measure":
        measure(a.file)
    elif a.cmd == "mux":
        mux(a.video, a.audio, a.out, a.bitrate)
    else:
        cfg_path = os.path.abspath(a.config)
        with open(cfg_path) as f:
            cfg = json.load(f)
        base = os.path.normpath(os.path.join(os.path.dirname(cfg_path), cfg.get("root", ".")))
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        if a.keep_premix:
            pre = os.path.splitext(a.out)[0] + "_premix.wav"
        else:
            with tempfile.NamedTemporaryFile(suffix="_premix.wav", dir=os.path.dirname(os.path.abspath(a.out)),
                                             delete=False) as tf:
                pre = tf.name
        graph = premix(cfg, base, pre)
        if a.print_graph:
            print(graph.replace(";", ";\n"))
        try:
            master(pre, a.out, cfg)
        finally:
            if not a.keep_premix and os.path.exists(pre):
                os.remove(pre)
        measure(a.out)


if __name__ == "__main__":
    main()
