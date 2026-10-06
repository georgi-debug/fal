#!/usr/bin/env python3
"""Python-API fallback runner for fal jobs, for when the fal MCP server is unavailable.

Runs a JSON list of jobs through the fal Python client (fal_client) with N parallel
workers. Every job is reserved in, and settled to, the same ledger that ledger.py
manages, so MCP work and fallback work share one ledger and one hard cap.

Usage
    python3 falgen.py jobs.json [--workers 6] [--out gens] [--timeout 3600] [--dry-run]
    python3 falgen.py jobs.json --dry-run      # print estimates and cap decisions only

    jobs.json = [
      {"tag": "G1_A2_d1", "endpoint": "bytedance/seedance-2.5/image-to-video",
       "args": {"prompt": "...", "image_url": "file://kf/A2.png", "duration": "5",
                "resolution": "480p", "draft": true},
       "est": 1.03},                               # optional; else estimated from args
      ...
    ]

    - Strings starting with "file://" anywhere in args are uploaded first (fal storage) and
      replaced by the URL. Uploads are cached by content hash in FAL_UPLOAD_CACHE.
    - Arg keys starting with "_" are estimator hints only and are not sent
      (_duration for draft/complete, _input_video_s for video inputs).
    - A job whose <out>/<tag>/result.json exists is skipped (no reservation, no cost).
    - Outputs (every http(s) "url" in the result) are downloaded to <out>/<tag>/out_<i>.<ext>;
      request.json, result.json or error.json are written alongside.

Environment
    FAL_KEY            fal API key (<key-id>:<key-secret>). If unset, FAL_MCP_KEY is used.
    FAL_BUDGET_CAP     hard cap in dollars (required), shared with ledger.py
    FAL_GROUP_CAPS     optional per-group caps, e.g. "G1:16,G2:18"
    FAL_LEDGER         ledger path (default ./fal_ledger.jsonl)
    FAL_PRICES         optional price overrides (see ledger.py)
    FAL_UPLOAD_CACHE   upload cache path (default ./fal_uploads.json)

Ledger outcomes
    success                    -> settle ok
    fal returned an error      -> settle failed (not billed; content-policy / likeness-filter
                                  rejections are free). Retry under a NEW tag.
    timeout / network failure  -> left PENDING (counted as spent): the job may still be
                                  running and billed. Check it by request id, then run
                                  `ledger.py settle --tag T --status ok|failed`.

Exit codes
    0  every job succeeded or was skipped
    1  usage / configuration error (bad job file, FAL_KEY or FAL_BUDGET_CAP missing)
    2  at least one job was refused by the budget or a group cap
    6  at least one job failed, timed out, or was refused for another reason
       (unknown endpoint without "est", duplicate tag)
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger  # noqa: E402  (same folder)

_upload_lock = threading.Lock()
_print_lock = threading.Lock()
_fal = None


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def fal():
    """Import fal_client lazily so --dry-run and --help work without it or a key."""
    global _fal
    if _fal is None:
        if not os.environ.get("FAL_KEY") and os.environ.get("FAL_MCP_KEY"):
            os.environ["FAL_KEY"] = os.environ["FAL_MCP_KEY"]
        if not os.environ.get("FAL_KEY"):
            raise ledger.ConfigError("FAL_KEY is not set (format <key-id>:<key-secret>).")
        import fal_client
        _fal = fal_client
    return _fal


# ---------------------------------------------------------------------------- uploads
def upload_cache_path():
    return os.path.abspath(os.environ.get("FAL_UPLOAD_CACHE") or "fal_uploads.json")


def _load_cache():
    p = upload_cache_path()
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(path):
    path = os.path.abspath(os.path.expanduser(path))
    h = file_hash(path)
    with _upload_lock:
        hit = _load_cache().get(h)
    if hit:
        return hit["url"]
    url = fal().upload_file(path)
    with _upload_lock:
        cache = _load_cache()
        cache[h] = {"path": path, "url": url, "t": round(time.time(), 3)}
        tmp = upload_cache_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=1)
        os.replace(tmp, upload_cache_path())
    log(f"[upload] {path} -> {url}")
    return url


def file_refs(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, str) and obj.startswith("file://"):
        out.append(obj[7:])
    elif isinstance(obj, list):
        for x in obj:
            file_refs(x, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            file_refs(v, out)
    return out


def resolve(obj):
    if isinstance(obj, str) and obj.startswith("file://"):
        return upload(obj[7:])
    if isinstance(obj, list):
        return [resolve(x) for x in obj]
    if isinstance(obj, dict):
        return {k: resolve(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------- downloads
def download(url, dest, tries=4):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(tries):
        try:
            urllib.request.urlretrieve(url, dest)
            return dest
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def collect_files(obj, out):
    if isinstance(obj, dict):
        u = obj.get("url")
        if isinstance(u, str) and u.startswith("http"):
            out.append(obj)
        for v in obj.values():
            collect_files(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_files(v, out)
    return out


def _dump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)


# ---------------------------------------------------------------------------- jobs
class JobFailed(Exception):
    def __init__(self, msg, kind):
        super().__init__(msg)
        self.kind = kind  # "refused" | "unknown" | "duplicate" | "failed" | "timeout"


def job_estimate(job):
    if job.get("est") is not None:
        return float(job["est"])
    return ledger.estimate_from_args(job["endpoint"], job.get("args", {}))


def run(endpoint, args, tag, est=None, group=None, out_root="gens", timeout=3600):
    """Reserve, upload, submit, wait, download, settle. Returns the result record."""
    outdir = os.path.join(out_root, tag)
    done = os.path.join(outdir, "result.json")
    if os.path.exists(done):
        log(f"[skip] {tag} already done ({done})")
        with open(done) as f:
            return json.load(f)
    est = ledger.estimate_from_args(endpoint, args) if est is None else float(est)
    try:
        r = ledger.reserve(tag, endpoint, est, group=group)
    except ledger.BudgetExceeded as e:
        raise JobFailed(str(e), "refused")
    except ledger.DuplicateTag as e:
        raise JobFailed(str(e), "duplicate")
    log(f"[reserve] {tag} ${est:.2f} | spent ${r['spent']:.2f} / cap ${r['cap']:.2f}")

    rid = None
    completed = False
    t0 = time.time()
    try:
        clean = resolve({k: v for k, v in args.items() if not k.startswith("_")})
        os.makedirs(outdir, exist_ok=True)
        _dump(os.path.join(outdir, "request.json"), {"endpoint": endpoint, "args": clean})
        client = fal()
        handle = client.submit(endpoint, arguments=clean)
        rid = handle.request_id
        ledger.record_request_id(tag, rid)
        log(f"[submit] {tag} rid={rid} est=${est:.2f}")
        # Poll status instead of resubmitting; resubmitting would be a new billable job.
        for status in handle.iter_events(interval=2.0):
            if isinstance(status, client.Completed):
                completed = True
                break
            if time.time() - t0 > timeout:
                break
        if not completed:
            raise TimeoutError(f"no result after {timeout}s")
        result = handle.get()
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        os.makedirs(outdir, exist_ok=True)
        _dump(os.path.join(outdir, "error.json"), {"error": msg, "request_id": rid})
        http_err = getattr(_fal, "FalClientHTTPError", ()) if _fal else ()
        if rid is None or (completed and http_err and isinstance(e, http_err)):
            # Never submitted, or the job completed with an error (e.g. content policy /
            # likeness filter): not billed.
            ledger.settle(tag, "failed", request_id=rid, note=msg[:500])
            log(f"[error] {tag}: {msg[:400]} (settled failed, reservation released)")
            raise JobFailed(msg, "failed")
        # Timeout / network error after submit: the job may still run and bill.
        log(f"[pending] {tag} rid={rid}: {msg[:300]}. Left PENDING (counted as spent); check "
            f"it, then: ledger.py settle --tag {tag} --status ok|failed")
        raise JobFailed(msg, "timeout")

    files = collect_files(result, [])
    local = []
    for i, fobj in enumerate(files):
        url = fobj["url"]
        ext = os.path.splitext(url.split("?")[0])[1] or ".bin"
        dest = os.path.join(outdir, f"out_{i}{ext}")
        try:
            download(url, dest)
            local.append(dest)
        except Exception as e:  # the job is billed anyway; keep the URL so it can be fetched
            log(f"[warn] {tag}: download failed for {url}: {e}")
    rec = {"endpoint": endpoint, "tag": tag, "request_id": rid, "result": result, "local": local,
           "elapsed": round(time.time() - t0, 1), "est": est}
    _dump(done, rec)
    ledger.settle(tag, "ok", request_id=rid, extra={"elapsed": rec["elapsed"]})
    log(f"[done] {tag} {rec['elapsed']}s -> {local}")
    return rec


def run_jobs(jobs, workers=6, out_root="gens", timeout=3600):
    out = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(run, j["endpoint"], j.get("args", {}), j["tag"], j.get("est"),
                          j.get("group"), out_root, timeout): j["tag"] for j in jobs}
        for fu in as_completed(futs):
            tag = futs[fu]
            try:
                out[tag] = fu.result()
            except JobFailed as e:
                out[tag] = {"error": str(e), "kind": e.kind}
            except ledger.LedgerError as e:
                kind = "unknown" if isinstance(e, ledger.UnknownEndpoint) else "failed"
                out[tag] = {"error": str(e), "kind": kind}
            except Exception as e:
                out[tag] = {"error": f"{type(e).__name__}: {e}", "kind": "failed"}
    return out


def dry_run(jobs, out_root="gens"):
    """Print estimates and cap decisions as if the jobs were reserved in order."""
    cap = ledger.budget_cap(required=False)
    gcaps = ledger.group_caps()
    rows = ledger.read_rows()
    refused = other = 0
    total = 0.0
    print(f"ledger {ledger.ledger_path()} | spent ${ledger.spent(rows):.2f} | cap "
          + (f"${cap:.2f}" if cap is not None else "UNSET (a real run would refuse)"))
    for j in jobs:
        tag, ep = j["tag"], j["endpoint"]
        if os.path.exists(os.path.join(out_root, tag, "result.json")):
            print(f"  SKIP     {tag:<28} {ep} (result.json exists)")
            continue
        try:
            est = job_estimate(j)
        except ledger.UnknownEndpoint:
            other += 1
            print(f"  UNKNOWN  {tag:<28} {ep}: no price; add \"est\" from get_pricing")
            continue
        group = ledger.group_of(tag, j.get("group"))
        missing = [p for p in file_refs(j.get("args", {}))
                   if not os.path.exists(os.path.expanduser(p))]
        try:
            ledger.check_reservation(rows, tag, est, group, cap if cap is not None else float("inf"),
                                     gcaps)
        except ledger.BudgetExceeded as e:
            refused += 1
            print(f"  REFUSE   {tag:<28} ${est:7.3f}  {e}")
            continue
        except ledger.LedgerError as e:
            other += 1
            print(f"  REFUSE   {tag:<28} ${est:7.3f}  {e}")
            continue
        rows.append({"tag": tag, "endpoint": ep, "group": group, "est": est, "status": "submitted"})
        total += est
        note = f"  MISSING FILES: {missing}" if missing else ""
        print(f"  RESERVE  {tag:<28} ${est:7.3f}  {ep}  (group {group}, running ${ledger.spent(rows):.2f}){note}")
        if missing:
            other += 1
    print(f"would reserve ${total:.2f} for this batch; ledger would stand at ${ledger.spent(rows):.2f}"
          + (f" of ${cap:.2f}" if cap is not None else ""))
    return 2 if refused else (6 if other else 0)


def load_jobs(path):
    try:
        with open(path) as f:
            jobs = json.load(f)
    except (OSError, ValueError) as e:
        raise ledger.ConfigError(f"cannot read job file {path}: {e}")
    if not isinstance(jobs, list):
        raise ledger.ConfigError("job file must be a JSON list of jobs")
    tags = set()
    for j in jobs:
        if not isinstance(j, dict) or not j.get("tag") or not j.get("endpoint"):
            raise ledger.ConfigError(f"every job needs 'tag' and 'endpoint': {j!r}"[:300])
        if j["tag"] in tags:
            raise ledger.ConfigError(f"duplicate tag in job file: {j['tag']}")
        tags.add(j["tag"])
    return jobs


def main(argv=None):
    ap = argparse.ArgumentParser(description="fal job runner (MCP fallback) sharing ledger.py's cap",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 all ok/skipped, 1 usage/config, "
                                        "2 a job was refused by the cap, 6 a job failed.")
    ap.add_argument("jobs", help="JSON list of {tag, endpoint, args, est?, group?}")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=os.environ.get("FAL_GEN_DIR", "gens"),
                    help="output root; each job writes <out>/<tag>/ (default ./gens)")
    ap.add_argument("--timeout", type=float, default=3600, help="seconds to wait per job")
    ap.add_argument("--dry-run", action="store_true",
                    help="print estimates and reservation decisions; no fal calls, no writes")
    a = ap.parse_args(argv)
    try:
        jobs = load_jobs(a.jobs)
        if a.dry_run:
            return dry_run(jobs, a.out)
        ledger.budget_cap(required=True)
        fal()  # fail fast on a missing key
        res = run_jobs(jobs, a.workers, a.out, a.timeout)
    except ledger.LedgerError as e:
        print(f"falgen: {e}", file=sys.stderr)
        return 1
    r = ledger.report()
    print(f"[ledger] spent ${r['spent']:.2f} / cap ${r['cap']:.2f} (remaining ${r['remaining']:.2f}); "
          f"pending {r['counts']['pending']}")
    errs = {k: v for k, v in res.items() if "error" in v}
    if errs:
        print("ERRORS:\n" + json.dumps(errs, indent=1)[:4000], file=sys.stderr)
        return 2 if any(v["kind"] == "refused" for v in errs.values()) else 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
