#!/usr/bin/env python3
"""Append-only JSONL cost ledger with a hard budget cap for fal generation jobs.

The fal MCP server has no budget cap: every submit_job / run_model call is billed.
This script is the local discipline around it. Reserve the estimated cost BEFORE
submitting, settle the job AFTER it finishes. Safe for parallel agents: every
read-check-append runs under an exclusive fcntl lock on "<ledger>.lock".

Environment
    FAL_LEDGER       ledger path (default ./fal_ledger.jsonl)
    FAL_BUDGET_CAP   hard cap in dollars; required for `reserve`
    FAL_GROUP_CAPS   optional per-group caps, e.g. "G1:16,G2:18". A tag's group is
                     the part before its first underscore (G1_A2_d1 -> G1) unless
                     --group is given.
    FAL_PRICES       optional JSON file that overrides / extends PRICES below.

Usage
    ledger.py estimate <endpoint> [--duration S] [--resolution 480p|720p|1080p|1K|2K|4K]
                       [--draft] [--input-video-s S] [--num-images N]
                       [--music-seconds S] [--sfx-seconds S]
    ledger.py reserve --tag T --endpoint E (--est X | <estimator flags>) [--group G]
                      [--dry-run]
    ledger.py rid     --tag T --request-id R            # record the fal request id
    ledger.py settle  --tag T --status ok|refunded|failed|charged
                      [--request-id R] [--note TEXT]
    ledger.py report  [--by endpoint|group|status] [--json]

    Typical MCP loop:
        ledger.py reserve --tag G1_A2_d1 --endpoint bytedance/seedance-2.5/image-to-video \
            --duration 5 --draft
        (submit_job ... check_job ... get_job_result)
        ledger.py settle --tag G1_A2_d1 --status ok --request-id <id>

Settle statuses
    ok        job succeeded; the reservation stands as the cost.
    refunded  job was blocked / rejected and not billed (content-policy or likeness-filter
              rejections); a negative row cancels the reservation.
    failed    job failed and was not billed; same accounting as refunded.
    charged   job failed but WAS billed; the reservation stands.
    A reservation that is never settled counts as spent ("pending").

Accounting
    Cost = sum of `est` over `submitted` rows and refund rows (refunded/failed carry
    est = -reservation). All figures are list-price ESTIMATES, not invoices.

Exit codes
    0  success
    1  usage or configuration error (e.g. FAL_BUDGET_CAP unset, bad FAL_GROUP_CAPS)
    2  reservation refused: global or group cap would be exceeded (nothing written)
    3  unknown endpoint: no price known (call the MCP get_pricing tool, then pass --est)
    4  duplicate tag on reserve, or tag already settled on settle
    5  tag not found (settle / rid on a tag that was never reserved)
"""
import argparse
import contextlib
import fcntl
import fnmatch
import json
import os
import sys
import time

# ======================================================================================
# PRICE TABLE  (USD, list prices as of October 2026)
#
# PRICES DRIFT. These are planning numbers only. Before using an endpoint in a session,
# confirm its price with the fal MCP `get_pricing` tool. If it differs, either pass
# --est explicitly or override this table with a JSON file in env FAL_PRICES, e.g.
#   {"fal-ai/nano-banana-pro*": {"unit": "per_image", "price": 0.15, "4k_multiplier": 2.0},
#    "some/new-endpoint": 0.25}
# (a bare number means a flat per-call price). Keys are fnmatch patterns; an exact key
# wins, otherwise the longest matching pattern wins.
#
# Units
#   seedance_tokens  tokens = w * h * (duration + input_video_s) * 24 / 1024;
#                    cost = tokens / 1000 * rate[resolution].
#                    `draft` forces 480p; `force_resolution` fixes it (draft/complete).
#   per_image        price * num_images (* 4k_multiplier when resolution is 4K)
#   per_call         flat price per job
#   per_minute       price * max(min_minutes, seconds / 60)
#   per_second       price * seconds (default_seconds when not given)
# ======================================================================================
PRICES = {
    # Seedance 2.5 (bytedance). ~$0.206/s at 480p, ~$1.14/s at 1080p. US endpoints ~20% more.
    "bytedance/seedance-2.5/*": {
        "unit": "seedance_tokens",
        "rate_per_1k_tokens": {"480p": 0.0214, "720p": 0.0214, "1080p": 0.0234},
        "default_resolution": "720p", "default_duration": 10},
    "bytedance/seedance-2.5/us/*": {
        "unit": "seedance_tokens",
        "rate_per_1k_tokens": {"480p": 0.02568, "720p": 0.02568, "1080p": 0.02808},
        "default_resolution": "720p", "default_duration": 10},
    # Completing a 480p draft is billed as a 1080p render of the same duration.
    "bytedance/seedance-2.5/draft/complete": {
        "unit": "seedance_tokens",
        "rate_per_1k_tokens": {"1080p": 0.0234},
        "force_resolution": "1080p", "default_duration": 10},
    # Images
    "fal-ai/nano-banana-pro": {"unit": "per_image", "price": 0.15, "4k_multiplier": 2.0},
    "fal-ai/nano-banana-pro/edit": {"unit": "per_image", "price": 0.15, "4k_multiplier": 2.0},
    "fal-ai/nano-banana-2": {"unit": "per_image", "price": 0.08, "4k_multiplier": 2.0},
    "fal-ai/nano-banana-2/edit": {"unit": "per_image", "price": 0.08, "4k_multiplier": 2.0},
    "bytedance/seedream/v5/pro/*": {"unit": "per_image", "price": 0.0675},
    # Music
    "google/lyria-3.5": {"unit": "per_call", "price": 0.10},
    "elevenlabs/music*": {"unit": "per_minute", "price": 0.60, "min_minutes": 1.0,
                          "default_seconds": 60},
    "minimax/music-3": {"unit": "per_call", "price": 0.48},
    # Sound effects
    "*sound-effects*": {"unit": "per_second", "price": 0.002, "default_seconds": 10},
    # AI critique / review through an LLM router (~$0.30 per video critique)
    "openrouter/router/video": {"unit": "per_call", "price": 0.30},
    "openrouter/router/audio": {"unit": "per_call", "price": 0.07},
}

DIMS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}

EXIT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_UNKNOWN, EXIT_DUPLICATE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4, 5

REFUND_STATUSES = ("refunded", "failed")
SETTLE_STATUSES = ("ok", "refunded", "failed", "charged")


class LedgerError(Exception):
    exit_code = EXIT_USAGE


class ConfigError(LedgerError):
    exit_code = EXIT_USAGE


class BudgetExceeded(LedgerError):
    exit_code = EXIT_REFUSED


class UnknownEndpoint(LedgerError):
    exit_code = EXIT_UNKNOWN


class DuplicateTag(LedgerError):
    exit_code = EXIT_DUPLICATE


class TagNotFound(LedgerError):
    exit_code = EXIT_NOT_FOUND


# ---------------------------------------------------------------------------- config
def ledger_path(path=None):
    return os.path.abspath(path or os.environ.get("FAL_LEDGER") or "fal_ledger.jsonl")


def budget_cap(required=False):
    raw = os.environ.get("FAL_BUDGET_CAP", "").strip()
    if not raw:
        if required:
            raise ConfigError(
                "FAL_BUDGET_CAP is not set. Refusing to reserve without a hard cap. "
                "Ask the user for the budget and export FAL_BUDGET_CAP (dollars, set a few "
                "percent under the real budget so in-flight jobs cannot overshoot it).")
        return None
    try:
        cap = float(raw)
    except ValueError:
        raise ConfigError(f"FAL_BUDGET_CAP must be a number of dollars, got {raw!r}")
    if cap < 0:
        raise ConfigError("FAL_BUDGET_CAP must be >= 0")
    return cap


def group_caps():
    raw = os.environ.get("FAL_GROUP_CAPS", "").strip()
    caps = {}
    if not raw:
        return caps
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, dollars = part.rpartition(":")
        if not sep or not name.strip():
            raise ConfigError(f"FAL_GROUP_CAPS entry {part!r} is not GROUP:DOLLARS")
        try:
            caps[name.strip()] = float(dollars)
        except ValueError:
            raise ConfigError(f"FAL_GROUP_CAPS entry {part!r}: {dollars!r} is not a number")
    return caps


def group_of(tag, explicit=None):
    if explicit:
        return explicit
    return tag.split("_", 1)[0] if "_" in tag else tag


# ---------------------------------------------------------------------------- pricing
def price_table():
    table = dict(PRICES)
    override = os.environ.get("FAL_PRICES", "").strip()
    if override:
        try:
            with open(override) as f:
                extra = json.load(f)
        except (OSError, ValueError) as e:
            raise ConfigError(f"cannot read FAL_PRICES file {override!r}: {e}")
        if not isinstance(extra, dict):
            raise ConfigError("FAL_PRICES must contain a JSON object")
        for k, v in extra.items():
            table[k] = {"unit": "per_call", "price": float(v)} if isinstance(v, (int, float)) else v
    return table


def lookup_price(endpoint, table=None):
    table = price_table() if table is None else table
    if endpoint in table:
        return table[endpoint]
    matches = [k for k in table if fnmatch.fnmatchcase(endpoint, k)]
    if not matches:
        return None
    return table[max(matches, key=lambda k: (len(k.replace("*", "")), len(k)))]


def estimate(endpoint, duration=None, resolution=None, draft=False, input_video_s=0.0,
             num_images=None, music_seconds=None, sfx_seconds=None):
    """Estimated list price in dollars. Raises UnknownEndpoint if no price is known."""
    spec = lookup_price(endpoint)
    if spec is None:
        raise UnknownEndpoint(
            f"no price known for endpoint {endpoint!r}. Call the fal MCP get_pricing tool for "
            f"it, compute the job cost, and pass it explicitly: "
            f"ledger.py reserve --tag ... --endpoint {endpoint} --est <dollars> "
            f"(or add it to a FAL_PRICES JSON file).")
    unit = spec.get("unit", "per_call")
    if unit == "seedance_tokens":
        res = spec.get("force_resolution") or ("480p" if draft else
                                               (resolution or spec.get("default_resolution", "720p")))
        if res not in DIMS:
            raise ConfigError(f"resolution {res!r} not one of {sorted(DIMS)} for {endpoint}")
        rates = spec["rate_per_1k_tokens"]
        rate = rates.get(res, rates.get("default"))
        if rate is None:
            raise ConfigError(f"no token rate for {res} on {endpoint}")
        w, h = DIMS[res]
        dur = float(duration if duration is not None else spec.get("default_duration", 10))
        tokens = w * h * (dur + float(input_video_s or 0)) * 24 / 1024
        return round(tokens / 1000 * rate, 3)
    if unit == "per_image":
        n = int(num_images or 1)
        mult = float(spec.get("4k_multiplier", 1.0)) if str(resolution).upper() == "4K" else 1.0
        return round(float(spec["price"]) * n * mult, 4)
    if unit == "per_call":
        return round(float(spec["price"]), 4)
    if unit == "per_minute":
        secs = float(music_seconds if music_seconds is not None else spec.get("default_seconds", 60))
        return round(float(spec["price"]) * max(float(spec.get("min_minutes", 0)), secs / 60.0), 4)
    if unit == "per_second":
        secs = sfx_seconds if sfx_seconds is not None else (
            music_seconds if music_seconds is not None else duration)
        secs = float(secs if secs is not None else spec.get("default_seconds", 10))
        return round(float(spec["price"]) * secs, 4)
    raise ConfigError(f"unknown price unit {unit!r} for {endpoint}")


def estimate_from_args(endpoint, args):
    """Estimate from a fal input dict (as used by falgen.py job files).

    Reads resolution, duration ("auto" -> default), draft, num_images, music_length_ms,
    duration_seconds, plus private hints _duration (draft/complete) and _input_video_s.
    """
    dur = args.get("_duration", args.get("duration"))
    if dur in (None, "auto", ""):
        dur = None
    else:
        dur = float(str(dur).rstrip("s"))
    music_s = args.get("music_length_ms")
    music_s = float(music_s) / 1000.0 if music_s is not None else None
    sfx_s = args.get("duration_seconds")
    return estimate(endpoint, duration=dur, resolution=args.get("resolution"),
                    draft=bool(args.get("draft", False)),
                    input_video_s=float(args.get("_input_video_s", 0) or 0),
                    num_images=args.get("num_images"), music_seconds=music_s,
                    sfx_seconds=float(sfx_s) if sfx_s not in (None, "") else None)


# ---------------------------------------------------------------------------- ledger io
@contextlib.contextmanager
def locked(path=None):
    """Exclusive cross-process (and cross-thread) lock around a read-check-append."""
    path = ledger_path(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def read_rows(path=None):
    path = ledger_path(path)
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a torn or hand-edited line never breaks accounting
            if isinstance(r, dict):
                rows.append(r)
    return rows


def _append(path, rec):
    rec = dict(rec)
    rec.setdefault("t", round(time.time(), 3))
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return rec


def _cost_rows(rows):
    return [r for r in rows if r.get("status") == "submitted" or r.get("status") in REFUND_STATUSES]


def spent(rows, group=None):
    tot = 0.0
    for r in _cost_rows(rows):
        if group is None or r.get("group") == group:
            tot += float(r.get("est", 0) or 0)
    return round(tot, 6)


def jobs(rows):
    """Collapse rows into one record per tag: endpoint, group, est, outcome, request_id."""
    out = {}
    for r in rows:
        tag = r.get("tag")
        if not tag:
            continue
        st = r.get("status")
        if st == "submitted":
            out[tag] = {"tag": tag, "endpoint": r.get("endpoint"), "group": r.get("group"),
                        "est": float(r.get("est", 0) or 0), "cost": float(r.get("est", 0) or 0),
                        "outcome": "pending", "request_id": r.get("request_id"), "t": r.get("t")}
            continue
        j = out.get(tag)
        if j is None:
            continue
        if st == "rid" and r.get("request_id"):
            j["request_id"] = r["request_id"]
        elif st in SETTLE_STATUSES:
            j["outcome"] = st
            if r.get("request_id"):
                j["request_id"] = r["request_id"]
            if st in REFUND_STATUSES:
                j["cost"] = round(j["cost"] + float(r.get("est", 0) or 0), 6)
    return out


def check_reservation(rows, tag, est, group, cap, gcaps):
    """Pure decision. Returns None if allowed, else raises the matching LedgerError."""
    if any(r.get("tag") == tag and r.get("status") == "submitted" for r in rows):
        raise DuplicateTag(
            f"tag {tag!r} is already in the ledger. Resubmitting creates a NEW billable job, "
            f"so a retry needs a new tag (e.g. {tag}_r2).")
    if est < 0:
        raise ConfigError("estimate must be >= 0")
    s = spent(rows)
    if s + est > cap + 1e-9:
        raise BudgetExceeded(
            f"BUDGET CAP: spent ${s:.2f} + est ${est:.2f} = ${s + est:.2f} > cap ${cap:.2f}. "
            f"Not reserved. Stop and tell the user.")
    if group in gcaps:
        gs = spent(rows, group)
        if gs + est > gcaps[group] + 1e-9:
            raise BudgetExceeded(
                f"GROUP CAP {group}: spent ${gs:.2f} + est ${est:.2f} = ${gs + est:.2f} > "
                f"group cap ${gcaps[group]:.2f}. Not reserved.")


def reserve(tag, endpoint, est, group=None, path=None, dry_run=False, extra=None):
    """Reserve `est` dollars for `tag` or raise. Returns a dict with spend figures."""
    if not tag or not endpoint:
        raise ConfigError("tag and endpoint are required")
    cap = budget_cap(required=True)
    gcaps = group_caps()
    group = group_of(tag, group)
    est = round(float(est), 6)
    with locked(path) as p:
        rows = read_rows(p)
        check_reservation(rows, tag, est, group, cap, gcaps)
        rec = {"tag": tag, "endpoint": endpoint, "group": group, "est": est, "status": "submitted"}
        if extra:
            rec.update(extra)
        if not dry_run:
            _append(p, rec)
            rows.append(rec)
        s = spent(rows) + (est if dry_run else 0)
        gs = spent(rows, group) + (est if dry_run else 0)
    res = {"tag": tag, "group": group, "est": est, "spent": round(s, 4), "cap": cap,
           "remaining": round(cap - s, 4), "dry_run": dry_run}
    if group in gcaps:
        res.update(group_spent=round(gs, 4), group_cap=gcaps[group],
                   group_remaining=round(gcaps[group] - gs, 4))
    return res


def _find_job(rows, tag):
    j = jobs(rows).get(tag)
    if j is None:
        raise TagNotFound(f"tag {tag!r} has no reservation in the ledger")
    return j


def record_request_id(tag, request_id, path=None):
    with locked(path) as p:
        rows = read_rows(p)
        j = _find_job(rows, tag)
        return _append(p, {"tag": tag, "endpoint": j["endpoint"], "group": j["group"], "est": 0,
                           "status": "rid", "request_id": request_id})


def settle(tag, status, request_id=None, note=None, path=None, extra=None):
    if status not in SETTLE_STATUSES:
        raise ConfigError(f"status must be one of {SETTLE_STATUSES}")
    with locked(path) as p:
        rows = read_rows(p)
        j = _find_job(rows, tag)
        if j["outcome"] != "pending":
            raise DuplicateTag(f"tag {tag!r} is already settled as {j['outcome']!r}")
        est = -j["est"] if status in REFUND_STATUSES else 0
        rec = {"tag": tag, "endpoint": j["endpoint"], "group": j["group"], "est": est,
               "status": status}
        if request_id or j.get("request_id"):
            rec["request_id"] = request_id or j.get("request_id")
        if note:
            rec["note"] = note
        if extra:
            rec.update(extra)
        _append(p, rec)
        rows.append(rec)
        s = spent(rows)
    return {"tag": tag, "status": status, "est": est, "spent": round(s, 4)}


def report(by=None, path=None):
    rows = read_rows(path)
    cap = budget_cap(required=False)
    gcaps = group_caps()
    js = jobs(rows)
    s = spent(rows)
    counts = {}
    for j in js.values():
        counts[j["outcome"]] = counts.get(j["outcome"], 0) + 1
    out = {"ledger": ledger_path(path), "spent": round(s, 4), "cap": cap,
           "remaining": None if cap is None else round(cap - s, 4),
           "jobs": len(js), "counts": {k: counts.get(k, 0) for k in ("ok", "refunded", "failed",
                                                                     "charged", "pending")},
           "pending_tags": sorted(t for t, j in js.items() if j["outcome"] == "pending")}
    groups = {}
    for j in js.values():
        g = j.get("group")
        groups.setdefault(g, 0.0)
        groups[g] += j["cost"]
    out["group_caps"] = {g: {"cap": c, "spent": round(groups.get(g, 0.0), 4),
                             "remaining": round(c - groups.get(g, 0.0), 4)}
                         for g, c in gcaps.items()}
    if by:
        key = {"endpoint": "endpoint", "group": "group", "status": "outcome"}[by]
        bd = {}
        for j in js.values():
            k = str(j.get(key))
            b = bd.setdefault(k, {"spent": 0.0, "jobs": 0, "ok": 0, "refunded": 0, "failed": 0,
                                  "charged": 0, "pending": 0})
            b["spent"] = round(b["spent"] + j["cost"], 6)
            b["jobs"] += 1
            b[j["outcome"]] += 1
        out["by"] = by
        out["breakdown"] = dict(sorted(bd.items(), key=lambda kv: -kv[1]["spent"]))
    return out


# ---------------------------------------------------------------------------- CLI
class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 1; exit 2 is reserved for "refused"
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _add_estimator_flags(p):
    p.add_argument("--duration", type=float, help="video seconds (Seedance)")
    p.add_argument("--resolution", help="480p|720p|1080p for video; 1K|2K|4K for images")
    p.add_argument("--draft", action="store_true", help="Seedance draft (forces 480p)")
    p.add_argument("--input-video-s", type=float, default=0.0,
                   help="seconds of input/reference video billed as tokens (Seedance)")
    p.add_argument("--num-images", type=int, help="images per job")
    p.add_argument("--music-seconds", type=float, help="music length in seconds")
    p.add_argument("--sfx-seconds", type=float, help="sound-effect length in seconds")


def _est_kwargs(a):
    return dict(duration=a.duration, resolution=a.resolution, draft=a.draft,
                input_video_s=a.input_video_s, num_images=a.num_images,
                music_seconds=a.music_seconds, sfx_seconds=a.sfx_seconds)


def _money(x):
    return "n/a" if x is None else f"${x:,.2f}"


def _print_report(r):
    print(f"ledger    {r['ledger']}")
    print(f"spent     {_money(r['spent'])}  (list-price estimates; pending counts as spent)")
    print(f"cap       {_money(r['cap'])}" + ("" if r["cap"] is not None else "  (FAL_BUDGET_CAP unset)"))
    print(f"remaining {_money(r['remaining'])}")
    c = r["counts"]
    print(f"jobs      {r['jobs']}: ok {c['ok']}, refunded {c['refunded']}, failed {c['failed']}, "
          f"charged {c['charged']}, pending {c['pending']}")
    if r["pending_tags"]:
        shown = ", ".join(r["pending_tags"][:20])
        more = "" if len(r["pending_tags"]) <= 20 else f" (+{len(r['pending_tags']) - 20} more)"
        print(f"pending   {shown}{more}")
    for g, v in r["group_caps"].items():
        print(f"group {g}: spent {_money(v['spent'])} / cap {_money(v['cap'])}, "
              f"remaining {_money(v['remaining'])}")
    if r.get("breakdown"):
        print(f"\nby {r['by']}:")
        w = max(len(k) for k in r["breakdown"])
        for k, b in r["breakdown"].items():
            print(f"  {k:<{w}}  {_money(b['spent']):>10}  jobs {b['jobs']:>4}  ok {b['ok']}  "
                  f"refunded {b['refunded']}  failed {b['failed']}  charged {b['charged']}  "
                  f"pending {b['pending']}")


def main(argv=None):
    ap = _Parser(description="fal cost ledger with a hard budget cap",
                 formatter_class=argparse.RawDescriptionHelpFormatter,
                 epilog="Exit codes: 0 ok, 1 usage/config, 2 over cap (refused), "
                        "3 unknown endpoint, 4 duplicate tag / already settled, 5 tag not found.")
    ap.add_argument("--ledger", help="ledger path (overrides FAL_LEDGER)")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    p = sub.add_parser("estimate", help="print the estimated cost of one job in dollars")
    p.add_argument("endpoint")
    _add_estimator_flags(p)

    p = sub.add_parser("reserve", help="reserve the estimate before submitting a job")
    p.add_argument("--tag", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--est", type=float, help="explicit estimate in dollars (from get_pricing)")
    p.add_argument("--group", help="budget group (default: tag prefix before first '_')")
    p.add_argument("--dry-run", action="store_true", help="check only, write nothing")
    _add_estimator_flags(p)

    p = sub.add_parser("rid", help="record the fal request id for a reserved tag")
    p.add_argument("--tag", required=True)
    p.add_argument("--request-id", required=True)

    p = sub.add_parser("settle", help="record the outcome of a reserved job")
    p.add_argument("--tag", required=True)
    p.add_argument("--status", required=True, choices=SETTLE_STATUSES)
    p.add_argument("--request-id")
    p.add_argument("--note")

    p = sub.add_parser("report", help="spend, cap, remaining, job counts")
    p.add_argument("--by", choices=("endpoint", "group", "status"))
    p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "estimate":
            print(f"{estimate(a.endpoint, **_est_kwargs(a)):.4f}")
        elif a.cmd == "reserve":
            est = a.est if a.est is not None else estimate(a.endpoint, **_est_kwargs(a))
            r = reserve(a.tag, a.endpoint, est, group=a.group, path=a.ledger, dry_run=a.dry_run)
            verb = "WOULD RESERVE" if a.dry_run else "reserved"
            line = (f"{verb} {r['tag']} ${r['est']:.2f} | spent ${r['spent']:.2f} / cap "
                    f"${r['cap']:.2f} | remaining ${r['remaining']:.2f}")
            if "group_cap" in r:
                line += (f" | group {r['group']} ${r['group_spent']:.2f} / ${r['group_cap']:.2f}"
                         f" (remaining ${r['group_remaining']:.2f})")
            print(line)
        elif a.cmd == "rid":
            record_request_id(a.tag, a.request_id, path=a.ledger)
            print(f"recorded request id for {a.tag}")
        elif a.cmd == "settle":
            r = settle(a.tag, a.status, request_id=a.request_id, note=a.note, path=a.ledger)
            print(f"settled {r['tag']} {r['status']} (est {r['est']:+.2f}) | spent ${r['spent']:.2f}")
        elif a.cmd == "report":
            r = report(by=a.by, path=a.ledger)
            if a.json:
                print(json.dumps(r, indent=1))
            else:
                _print_report(r)
    except LedgerError as e:
        print(f"ledger: {e}", file=sys.stderr)
        return e.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
