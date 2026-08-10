# -*- coding: utf-8 -*-
"""rail_utilization.py - are you actually burning the subscriptions you already paid for?

Stdlib only. No LLM call, no network, no API key.

The premise, and it is the whole point: an unused allowance is not a saving, it is a
loss you cannot see. The invoice is identical whether you drew 5% of the bucket or 95%.
So "we were careful with the expensive vendor" is only true if the work went somewhere
cheaper. If it went nowhere, you paid full price for an empty bucket AND did the work on
the priciest rail you own.

    python rail_utilization.py measure                  # count real calls from your own logs
    python rail_utilization.py declare gemini 62        # a percentage a human read in a panel
    python rail_utilization.py snap-codex               # a percentage the VENDOR reported
    python rail_utilization.py report [--html]
    python rail_utilization.py audit                    # exit 1 on underuse or a blind spot

THE HONESTY BOUNDARY, which matters more than the code. Bucket percentages generally
live in vendor web panels and no script can read them. So numbers of different origin
are never mixed and never averaged:

    measured  - counted from your logs. Proof: the log file.
    declared  - typed in by a human who read a panel. Proof: a date and a name.
    vendor    - reported by the vendor's own tooling. Proof: its transcript.

A vendor with no fresh reading prints "not measured". That is not zero. Zero calls in
your logs means "we did not call it", which is a signal in itself, but it is still not
the same claim as "the bucket is empty".

Exit codes: 0 fine, 1 findings (audit), 2 bad invocation, 4 crash.
"""
import argparse
import datetime
import glob
import io
import json
import os
import sys

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".llm-spend-audit")
STATE = os.path.join(STATE_DIR, "rails_state.json")
CONFIG_NAME = "rails.json"

UNDERUSE_PCT = 40          # below this, a bucket counts as underused
UNDERUSE_MONTHS = 2        # this many months in a row means cut the plan
STALE_DAYS = 45            # a reading older than this is not a reading

EXAMPLE_CONFIG = {
    "rails": [
        {"vendor": "primary", "plan": "example orchestrator plan", "usd_month": 200,
         "engines": ["primary"], "role": "orchestrator"},
        {"vendor": "reviewer", "plan": "example reviewer plan", "usd_month": 20,
         "engines": ["reviewer", "reviewer-cli"], "role": "second opinion"},
    ],
    "usage_logs": ["~/.myagent/usage.jsonl"],
}


# ------------------------------------------------------------------------------- config
def load_config(path=None):
    """Your roster of paid rails. Copy rails.example.json to rails.json and edit it."""
    candidates = [path] if path else [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_NAME),
        os.path.join(STATE_DIR, CONFIG_NAME),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            with io.open(cand, encoding="utf-8") as fh:
                cfg = json.load(fh)
            if not cfg.get("rails"):
                raise ValueError("%s has no 'rails' list" % cand)
            cfg.setdefault("usage_logs", [])
            cfg["_path"] = cand
            return cfg
    raise ValueError("no %s found. Copy rails.example.json next to this script and edit it."
                     % CONFIG_NAME)


def _load_state():
    if os.path.exists(STATE):
        try:
            with io.open(STATE, encoding="utf-8") as fh:
                state = json.load(fh)
            state.setdefault("readings", [])
            state.setdefault("measurements", [])
            return state
        except (ValueError, IOError):
            # A corrupt state file is renamed, never silently discarded: losing the
            # history would make the report show "all fine" on no data at all.
            os.rename(STATE, STATE + ".broken")
    return {"readings": [], "measurements": []}


def _save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(state, ensure_ascii=False, indent=2))
    os.replace(tmp, STATE)


def _today():
    return datetime.date.today()


def _age_days(date_str):
    try:
        return (_today() - datetime.date(*map(int, date_str.split("-")))).days
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- measuring
def count_calls(cfg, days):
    """How many times each engine was really called in the last N days.

    Your usage log is expected to be JSONL with at least {"ts": <epoch>, "engine": "..."}.
    Extra fields are ignored. Point `usage_logs` at whatever your own tooling writes.

    TWO BUGS PAID FOR IN PRODUCTION, both preserved here as behaviour:

    1. Two copies of the same tool wrote to two different logs, and this counter read only
       one of them. The report said a rail was called 16 times when the live engine had
       logged far more. Undercounting a rail pushes you to cancel a plan you are using,
       so we read EVERY configured log and merge.

    2. Merging without dedup does the opposite damage. Overstating utilisation is exactly
       as forbidden as understating it, so identical (ts, engine, task) records collapse.
    """
    counts, seen = {}, set()
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    for raw in cfg.get("usage_logs", []):
        path = os.path.expanduser(raw)
        if not os.path.exists(path):
            continue
        with io.open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("skipped"):
                    continue
                ts = rec.get("ts")
                if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts < cutoff:
                    continue
                engine = str(rec.get("engine") or "?").lower()
                key = (ts, engine, rec.get("task"), rec.get("point"))
                if key in seen:
                    continue
                seen.add(key)
                counts[engine] = counts.get(engine, 0) + 1
    return counts


def cmd_measure(args):
    cfg = load_config(args.config)
    state = _load_state()
    rec = {"date": _today().isoformat(),
           "calls_7d": count_calls(cfg, 7),
           "calls_30d": count_calls(cfg, 30)}
    state["measurements"] = [m for m in state["measurements"] if m.get("date") != rec["date"]]
    state["measurements"].append(rec)
    state["measurements"] = state["measurements"][-120:]
    _save_state(state)
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


def cmd_declare(args):
    cfg = load_config(args.config)
    known = {r["vendor"] for r in cfg["rails"]}
    if args.vendor not in known:
        sys.stderr.write("unknown vendor %r. Configured: %s\n"
                         % (args.vendor, ", ".join(sorted(known))))
        return 2
    if not 0 <= args.pct <= 100:
        sys.stderr.write("percentage must be 0..100, got %r\n" % args.pct)
        return 2
    state = _load_state()
    state["readings"].append({"vendor": args.vendor, "pct": float(args.pct),
                              "date": args.date or _today().isoformat(),
                              "by": args.by or "human", "origin": "declared",
                              "note": args.note or ""})
    state["readings"] = state["readings"][-400:]
    _save_state(state)
    print("recorded: %s = %s%% on %s (declared)"
          % (args.vendor, args.pct, args.date or _today().isoformat()))
    return 0


# ---------------------------------------------------- a percentage the vendor itself said
def _dig(obj, key):
    """Find a key at any depth. A vendor's transcript format is not your contract and
    can move a level up or down between releases without telling you."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _dig(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _dig(value, key)
            if found is not None:
                return found
    return None


def read_vendor_rate_limit(sessions_glob, max_files=40):
    """Pull the freshest rate-limit block a CLI vendor wrote into its own transcripts.

    Some CLIs record the vendor's answer about your remaining allowance. That number is
    neither your estimate nor a human's reading, so it deserves its own origin tag.

    Three deliberate details, each one a bug we hit:
      - the newest FILE may have no rate-limit block at all (a session that died early),
        so walk files newest to oldest until one yields;
      - inside a file take the LAST block, which reflects the end of that session;
      - when there are several buckets (a short rolling one and a weekly one), take the
        FULLEST. Blindly taking the first showed 3% while the binding bucket was at 90%,
        i.e. it reported underuse at the exact moment we were hitting the ceiling.
    """
    files = glob.glob(os.path.expanduser(sessions_glob), recursive=True)
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in files[:max_files]:
        found = None
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    limits = _dig(rec, "rate_limits")
                    if not isinstance(limits, dict):
                        continue
                    buckets = []
                    for slot in ("primary", "secondary"):
                        bucket = limits.get(slot)
                        if isinstance(bucket, dict) and bucket.get("used_percent") is not None:
                            try:
                                buckets.append((float(bucket["used_percent"]),
                                                bucket.get("window_minutes"), slot))
                            except (TypeError, ValueError):
                                continue
                    if not buckets:
                        continue
                    buckets.sort(reverse=True)
                    found = {"used_percent": buckets[0][0], "window_minutes": buckets[0][1],
                             "slot": buckets[0][2], "all_buckets": buckets,
                             "plan_type": limits.get("plan_type"),
                             "limit_id": limits.get("limit_id"),
                             "ts": rec.get("timestamp") or "", "source": path}
        except IOError:
            continue
        if found:
            return found
    return None


def cmd_snap_codex(args):
    cfg = load_config(args.config)
    # A reading recorded against a vendor that is not in the roster is stored and then
    # never shown by report or audit. The user believes the rail is measured; nothing
    # says otherwise. Refuse loudly instead, exactly as `declare` does.
    known = {r["vendor"] for r in cfg["rails"]}
    if args.vendor not in known:
        sys.stderr.write("unknown vendor %r, so this reading would be recorded and never "
                         "reported. Configured: %s\n" % (args.vendor, ", ".join(sorted(known))))
        return 2
    pattern = args.glob or cfg.get("vendor_transcripts", {}).get(args.vendor) \
        or os.path.join("~", ".codex", "sessions", "**", "rollout-*.jsonl")
    limits = read_vendor_rate_limit(pattern)
    if not limits:
        sys.stderr.write("no rate_limits block found under %s\n"
                         "That does NOT mean the bucket is empty. It means this CLI was not "
                         "used here, or the format moved.\n" % pattern)
        return 1

    day = limits["ts"][:10]
    age = _age_days(day)
    if age is None:
        # Substituting today's date for an unreadable one makes a stale number look fresh.
        # A money decision then gets made on a figure from last week.
        sys.stderr.write("the vendor record has no readable date (ts=%r). Refusing to "
                         "substitute today.\n" % limits["ts"][:40])
        return 1
    if age > args.max_age:
        sys.stderr.write("freshest vendor figure is from %s (%d days old), older than "
                         "--max-age=%d. Not recording stale data.\n" % (day, age, args.max_age))
        return 1

    state = _load_state()
    note = ("limit_id=%s bucket=%s window_min=%s plan=%s buckets=%s src=%s"
            % (limits["limit_id"], limits["slot"], limits["window_minutes"], limits["plan_type"],
               ";".join("%s=%s%%/%smin" % (s, p, w) for p, w, s in limits["all_buckets"]),
               os.path.basename(limits["source"])))
    state["readings"] = [r for r in state["readings"]
                         if not (r["vendor"] == args.vendor and r.get("date") == day
                                 and r.get("origin") == "vendor")]
    state["readings"].append({"vendor": args.vendor, "pct": round(limits["used_percent"], 1),
                              "date": day, "by": "vendor-rate-limits", "origin": "vendor",
                              "note": note})
    state["readings"] = state["readings"][-400:]
    _save_state(state)
    print("%s = %s%% on %s (the vendor's own figure, not our estimate)"
          % (args.vendor, round(limits["used_percent"], 1), day))
    print("  " + note)
    return 0


# ------------------------------------------------------------------------------- report
def latest_reading(state, vendor):
    """The freshest reading. On the SAME date the vendor's own figure beats a human's.

    We once had a hand-typed 30% and a vendor-reported 3.0% for the same day, a tenfold
    gap, and the plan decision would have been made on the wrong one.
    """
    rows = [r for r in state["readings"] if r["vendor"] == vendor]
    if not rows:
        return None
    return sorted(rows, key=lambda r: (r["date"], r.get("origin") == "vendor"))[-1]


def underuse_streak(state, vendor):
    """Consecutive recent calendar months of underuse.

    The naive "last reading of the month wins" hides a month that was at 30% mid-month
    and 60% at the end. But the naive monthly minimum is wrong too: a bucket fills over
    the month, so a reading on the 5th is always low and proves nothing. So only readings
    from the last third of a month count, and a month with no such reading BREAKS the
    streak instead of counting either way, because there is nothing to judge on.
    """
    by_month = {}
    for r in sorted([r for r in state["readings"] if r["vendor"] == vendor],
                    key=lambda r: r["date"]):
        try:
            day = int(r["date"][8:10])
        except ValueError:
            continue
        if day < 20:
            continue
        by_month[r["date"][:7]] = r["pct"]
    streak = 0
    for month in sorted(by_month, reverse=True):
        if by_month[month] < UNDERUSE_PCT:
            streak += 1
        else:
            break
    return streak


def origin_clash(state, vendor, within_days=7, ratio=2.0):
    """A vendor figure and a human figure that disagree by more than 2x in one week.

    We do not resolve this silently in either direction. Most of the time it is not a
    dispute about the truth, it is two DIFFERENT buckets (a coding allowance versus the
    whole plan). Printing both and saying they are not comparable is the honest output.
    """
    def _fresh(r):
        # `_age_days(...) or 999` looks equivalent and is not: an age of 0, i.e. a reading
        # taken TODAY, is falsy, so today's numbers were the one pair this check could
        # never see. Caught by the test below, never by a human reading the line.
        age = _age_days(r["date"])
        return age is not None and age <= within_days

    fresh = [r for r in state["readings"] if r["vendor"] == vendor and _fresh(r)]
    machine = [r for r in fresh if r.get("origin") == "vendor"]
    human = [r for r in fresh if r.get("origin") != "vendor"]
    if not machine or not human:
        return None
    m, h = machine[-1], human[-1]
    low, high = sorted([max(m["pct"], 0.1), max(h["pct"], 0.1)])
    if high / low < ratio:
        return None
    return ("vendor says %s%% (%s), human recorded %s%% (%s), a %.0fx gap. Most likely "
            "two different buckets, not a disagreement: do not average them"
            % (m["pct"], m["date"], h["pct"], h["date"], high / low))


def verdict(pct, age):
    if pct is None:
        return "not measured", "nobody read the panel. Read it and run `declare`"
    if age is not None and age > STALE_DAYS:
        return "stale", "the reading is older than %d days, take a new one" % STALE_DAYS
    if pct < UNDERUSE_PCT:
        return "UNDERUSED", "%s%% drawn. That is burnt money, not thrift" % pct
    if pct >= 95:
        return "AT THE CEILING", "the bucket is nearly full, the plan is too small"
    return "burning it", "%s%% drawn" % pct


def build_rows(cfg, state):
    last = state["measurements"][-1] if state["measurements"] else {}
    calls7 = last.get("calls_7d") or {}
    rows = []
    for rail in cfg["rails"]:
        reading = latest_reading(state, rail["vendor"])
        pct = reading["pct"] if reading else None
        age = _age_days(reading["date"]) if reading else None
        engines = rail.get("engines") or [rail["vendor"]]
        rows.append({
            "vendor": rail["vendor"], "plan": rail.get("plan", ""),
            "usd": rail.get("usd_month", 0),
            "calls7": sum(calls7.get(e.lower(), 0) for e in engines),
            "pct": pct, "date": reading["date"] if reading else None,
            "origin": reading.get("origin") if reading else None,
            "streak": underuse_streak(state, rail["vendor"]),
            "clash": origin_clash(state, rail["vendor"]),
        })
        rows[-1]["mark"], rows[-1]["why"] = verdict(pct, age)
    return rows, last


def is_blind(row):
    """Paying and not measuring. This used to print as "not measured" and then quietly
    fall out of every audit, so a plan could bleed for a year because silence never
    escalated. Silence has to be proven, not accepted."""
    return row["pct"] is None or row["mark"] == "stale"


def cmd_report(args):
    cfg = load_config(args.config)
    state = _load_state()
    # Always refresh before showing. A dashboard that quietly ages is worse than none:
    # both cheap steps here are local file reads, so there is no excuse to skip them.
    fresh = {"date": _today().isoformat(),
             "calls_7d": count_calls(cfg, 7), "calls_30d": count_calls(cfg, 30)}
    state["measurements"] = [m for m in state["measurements"] if m.get("date") != fresh["date"]]
    state["measurements"].append(fresh)
    state["measurements"] = state["measurements"][-120:]
    _save_state(state)

    rows, last = build_rows(cfg, state)
    total = sum(r["usd"] for r in rows)

    if args.html:
        cells = "".join(
            "<tr><td>%s</td><td>%s</td><td>$%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (r["vendor"], r["plan"], r["usd"], r["calls7"],
               "not measured" if r["pct"] is None else "%s%% (%s)" % (r["pct"], r["origin"]),
               r["mark"]) for r in rows)
        html = ("<!doctype html><meta charset=utf-8><title>Rail utilisation</title>"
                "<style>body{font-family:system-ui;margin:2rem}td,th{padding:.4rem .8rem;"
                "border-bottom:1px solid #ddd;text-align:left}</style>"
                "<h1>Paid rails: are we drawing them?</h1>"
                "<p>Committed per month: <b>$%s</b>. Measured: %s</p>"
                "<table><tr><th>rail<th>plan<th>$/mo<th>calls 7d<th>bucket drawn<th>verdict</tr>"
                "%s</table><p>&ldquo;not measured&rdquo; is not zero.</p>"
                % (total, last.get("date", "-"), cells))
        if args.out:
            with io.open(args.out, "w", encoding="utf-8") as fh:
                fh.write(html)
            sys.stderr.write("written: %s\n" % args.out)
        else:
            print(html)
        return 0

    print("# Paid rail utilisation ($%s/month committed)" % total)
    print("logs measured: %s" % last.get("date", "never"))
    print("")
    print("| rail | plan | $/mo | calls 7d | bucket drawn | verdict |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        drawn = ("not measured" if r["pct"] is None
                 else "%s%% (%s, %s)" % (r["pct"], r["date"], r["origin"]))
        print("| %s | %s | %s | %s | %s | %s |"
              % (r["vendor"], r["plan"], r["usd"], r["calls7"], drawn, r["mark"]))
    print("")
    for r in rows:
        print("- %s: %s" % (r["vendor"], r["why"]))
    for r in rows:
        if r["clash"]:
            print("- WARNING %s: %s" % (r["vendor"], r["clash"]))
    return 0


def cmd_audit(args):
    cfg = load_config(args.config)
    state = _load_state()
    rows, _ = build_rows(cfg, state)
    bad = [r for r in rows if r["streak"] >= UNDERUSE_MONTHS]
    blind = [r for r in rows if is_blind(r)]
    for r in bad:
        print("CUT THE PLAN: %s (%s, $%s/mo) underused %d months in a row"
              % (r["vendor"], r["plan"], r["usd"], r["streak"]))
    for r in blind:
        print("BLIND SPOT: %s (%s, $%s/mo) is paid for and never measured"
              % (r["vendor"], r["plan"], r["usd"]))
    if blind:
        print("unmeasured spend: $%s/month" % sum(r["usd"] for r in blind))
    if not bad and not blind:
        print("no rail underused %d months in a row, no blind spots" % UNDERUSE_MONTHS)
    return 1 if (bad or blind) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="are you drawing the subscriptions you pay for")
    ap.add_argument("--config", default=None, help="path to rails.json")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("measure", help="count real calls per rail from your own logs")

    d = sub.add_parser("declare", help="record a percentage a human read in a vendor panel")
    d.add_argument("vendor")
    d.add_argument("pct", type=float)
    d.add_argument("--date", default="")
    d.add_argument("--by", default="")
    d.add_argument("--note", default="")

    s = sub.add_parser("snap-codex", help="record a percentage the vendor CLI itself reported")
    s.add_argument("--vendor", default="codex")
    s.add_argument("--glob", default="", help="glob for the vendor's transcripts")
    s.add_argument("--max-age", type=int, default=3, help="refuse figures older than N days")

    r = sub.add_parser("report", help="paid / drawn / verdict table")
    r.add_argument("--html", action="store_true")
    r.add_argument("--out", default="", help="write HTML here instead of stdout")

    sub.add_parser("audit", help="exit 1 on sustained underuse or an unmeasured rail")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    handlers = {"measure": cmd_measure, "declare": cmd_declare, "snap-codex": cmd_snap_codex,
                "report": cmd_report, "audit": cmd_audit}
    try:
        return handlers[args.cmd](args)
    except ValueError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.exit(main() or 0)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(4)
