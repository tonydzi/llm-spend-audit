# -*- coding: utf-8 -*-
"""session_tax.py - what your own wiring charges you on EVERY session, before any work.

Stdlib only. No LLM call, no network, no API key. It reads the transcripts the harness
already wrote and does arithmetic on numbers the vendor itself recorded.

The axis this measures is the one people forget. Choosing a cheaper model is about what
you pay for the WORK. This is about the standing rent your automation charges: the
always-loaded rules file, the hook that prints eleven lines at startup, the tool
descriptions, the memory index. You build it once and pay for it in every session
forever, and it is only visible at the moment you build it.

    python session_tax.py calibrate            # chars per token, measured on your own data
    python session_tax.py session-start        # what a session costs before it does anything
    python session_tax.py daily                # per-day output and cache split
    python session_tax.py item-cost FILE       # rent for one permanent context addition
    python session_tax.py item-cost -          # same, reading stdin

Add --json to any command for machine-readable output.

Exit codes: 0 fine, 2 bad invocation, 4 crash.
"""
import argparse
import datetime
import json
import os
import re
import statistics
import sys

import transcripts as T

VERSION = "1.0.0"
STATE_DIR = os.path.join(os.path.expanduser("~"), ".llm-spend-audit")
CAL_PATH = os.path.join(STATE_DIR, "calibration.json")

CYRILLIC = re.compile(r"[Ѐ-ӿԀ-ԯ]")

# Used only until you run `calibrate`. Every printout says so, because an uncalibrated
# number presented as a measurement is the exact lie this kit exists to prevent.
FALLBACK = {"cyrillic_chars_per_token": 2.0,
            "other_chars_per_token": 3.6,
            "source": "fallback guess, NOT a measurement"}

# A message whose text is shorter than this, or whose token count is lower, is too small
# for the ratio to mean anything: framing overhead dominates.
MIN_TEXT_CHARS = 200
MIN_TEXT_TOKENS = 40
MIN_SAMPLES = 8
MIN_TREND_SESSIONS = 3     # a day with fewer sessions cannot anchor a growth claim


def _split_scripts(text):
    cyr = len(CYRILLIC.findall(text))
    return cyr, len(text) - cyr


# --------------------------------------------------------------------------- calibrate
def calibrate(days=30, root=None):
    """Measure characters-per-token from pairs the harness recorded for us.

    THE TRAP THAT COST US THE FIRST CALIBRATION, and the most useful line in this repo:

      output_tokens is charged for the WHOLE assistant message - thinking blocks and
      tool_use blocks included - and the same usage object is REPEATED in every
      transcript record belonging to that message.

    Divide visible text by that number and you get nonsense. Our first run reported 1.2
    characters per token for Cyrillic, which is physically impossible for that script,
    and we nearly shipped a context budget built on it.

    The fix is two lines: group records by message.id, and keep only messages whose
    content blocks are ALL of type "text". Those are the only ones where the recorded
    token count is actually the price of the text you can see.
    """
    msgs = {}
    for _, path in T.transcript_files(root, days=days, min_bytes=5 * 1024):
        msgs.update(T.assistant_messages(path))

    cyr_rates, other_rates, examined = [], [], 0
    for entry in msgs.values():
        if entry["kinds"] - {"text"}:
            continue                  # thinking or tool_use present: tokens are not about the text
        text, tokens = entry["text"], entry["tokens"]
        if len(text) < MIN_TEXT_CHARS or tokens < MIN_TEXT_TOKENS:
            continue
        cyr, other = _split_scripts(text)
        share = cyr / max(1, len(text))
        examined += 1
        # Only near-pure samples: a 50/50 mixed message cannot tell the two rates apart.
        if share > 0.6:
            cyr_rates.append(len(text) / tokens)
        elif share < 0.05:
            other_rates.append(len(text) / tokens)

    cal = dict(FALLBACK)
    cal.update({"examined": examined, "n_cyrillic": len(cyr_rates), "n_other": len(other_rates)})
    if len(cyr_rates) >= MIN_SAMPLES:
        cal["cyrillic_chars_per_token"] = round(statistics.median(cyr_rates), 3)
        cal["cyrillic_spread"] = [round(min(cyr_rates), 2), round(max(cyr_rates), 2)]
    if len(other_rates) >= MIN_SAMPLES:
        cal["other_chars_per_token"] = round(statistics.median(other_rates), 3)
        cal["other_spread"] = [round(min(other_rates), 2), round(max(other_rates), 2)]
    if len(cyr_rates) >= MIN_SAMPLES or len(other_rates) >= MIN_SAMPLES:
        cal["source"] = "measured on %d pure-text replies over %d days" % (examined, days)
        cal["calibrated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_calibration(cal)
    return cal


def _save_calibration(cal):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CAL_PATH, "w", encoding="utf-8") as fh:
            json.dump(cal, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass                                # a read-only home must not break the report


def load_calibration():
    try:
        with open(CAL_PATH, encoding="utf-8") as fh:
            cal = json.load(fh)
        if isinstance(cal, dict) and "cyrillic_chars_per_token" in cal:
            return cal
    except (OSError, ValueError):
        pass
    return dict(FALLBACK)


def is_measured(cal):
    return "measured" in str(cal.get("source", ""))


def estimate_tokens(text, cal=None):
    cal = cal or load_calibration()
    cyr, other = _split_scripts(text)
    return int(round(cyr / cal["cyrillic_chars_per_token"] + other / cal["other_chars_per_token"]))


# ----------------------------------------------------------------------- session start
def session_starts(days=14, root=None):
    """[(when, tokens, file)] - the context shipped on the FIRST request of each session.

    That first number is the whole preamble: system prompt, always-loaded rule files,
    tool descriptions, hook output, memory index. Nothing you asked for yet.
    """
    rows = []
    for ts, path in T.transcript_files(root, days=days, min_bytes=5 * 1024):
        for rec in T.records(path, needle='"usage"'):
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            total = T.context_tokens(msg.get("usage"))
            if total > 0:
                rows.append((ts, total, os.path.basename(path)))
                break
    return rows


def sessions_per_day(rows):
    per_day = {}
    for ts, _, _ in rows:
        per_day[ts.date()] = per_day.get(ts.date(), 0) + 1
    return statistics.median(list(per_day.values())) if per_day else 0


# ------------------------------------------------------------------------------ output
def _thousands(n):
    return "{:,}".format(int(n)).replace(",", " ")


def cmd_calibrate(args):
    cal = calibrate(args.days, args.root)
    if args.json:
        print(json.dumps(cal, ensure_ascii=False, indent=2))
        return 0
    print("TOKEN COUNTER CALIBRATION")
    print("  pure-text replies examined : %d" % cal.get("examined", 0))
    print("  Cyrillic   : %s chars/token  (n=%d, spread %s)"
          % (cal["cyrillic_chars_per_token"], cal.get("n_cyrillic", 0),
             cal.get("cyrillic_spread", "-")))
    print("  Latin/code : %s chars/token  (n=%d, spread %s)"
          % (cal["other_chars_per_token"], cal.get("n_other", 0),
             cal.get("other_spread", "-")))
    print("  source     : %s" % cal["source"])
    if not is_measured(cal):
        print("")
        print("  Not enough clean samples yet, so the fallback rates are still in use.")
        print("  Keep working and re-run. Nothing here should be quoted as a measurement.")
        return 0
    ratio = cal["other_chars_per_token"] / cal["cyrillic_chars_per_token"]
    print("")
    print("  Non-Latin text costs about %.1fx more per character than Latin." % ratio)
    print("  If your rules files are written in one script, budget them in that one.")
    return 0


def cmd_session_start(args):
    rows = session_starts(args.days, args.root)
    if not rows:
        print("No transcripts with usage data in the last %d days." % args.days)
        print("Looked in: %s" % T.projects_root(args.root))
        return 0
    values = [v for _, v, _ in rows]
    per_day = {}
    for ts, v, _ in rows:
        per_day.setdefault(ts.strftime("%Y-%m-%d"), []).append(v)
    trend = [(d, int(statistics.median(vs)), len(vs)) for d, vs in sorted(per_day.items())]
    if args.json:
        print(json.dumps({"days": args.days, "sessions": len(rows),
                          "median": int(statistics.median(values)),
                          "min": min(values), "max": max(values),
                          "by_day": [{"date": d, "median": m, "sessions": n}
                                     for d, m, n in trend]}, indent=2))
        return 0
    print("COST OF STARTING A SESSION (first request, before any work), last %d days" % args.days)
    print("  sessions : %d" % len(rows))
    print("  median   : %s tokens" % _thousands(statistics.median(values)))
    print("  min/max  : %s / %s" % (_thousands(min(values)), _thousands(max(values))))
    print("")
    print("  per day (median starting context):")
    for date, med, n in trend[-14:]:
        print("    %s  %9s tokens  (%d sessions)" % (date, _thousands(med), n))
    # Endpoints of the trend ignore days with almost no sessions. A day holding one
    # session has a "median" that is just that session, and anchoring a growth claim to
    # it turns noise into a headline.
    solid = [t for t in trend if t[2] >= MIN_TREND_SESSIONS]
    if len(solid) >= 2 and solid[0][1]:
        delta = solid[-1][1] - solid[0][1]
        sign = "+" if delta >= 0 else "-"
        print("")
        print("  %s to %s: %s%s tokens (%+.0f%%), days with %d+ sessions only."
              % (solid[0][0], solid[-1][0], sign, _thousands(abs(delta)),
                 100.0 * delta / solid[0][1], MIN_TREND_SESSIONS))
    print("")
    print("  A line that climbs is your own improvements getting more expensive.")
    print("  This is the single number worth watching every week.")
    return 0


def cmd_daily(args):
    per_day = {}
    for ts, path in T.transcript_files(args.root, days=args.days, min_bytes=5 * 1024):
        day = per_day.setdefault(ts.strftime("%Y-%m-%d"),
                                 {"sessions": 0, "output": 0, "cache_new": 0, "cache_read": 0})
        day["sessions"] += 1
        for rec in T.records(path, needle='"usage"'):
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            day["output"] += T.num(usage, "output_tokens")
            day["cache_new"] += T.num(usage, "cache_creation_input_tokens")
            day["cache_read"] += T.num(usage, "cache_read_input_tokens")
    if args.json:
        print(json.dumps(per_day, indent=2))
        return 0
    if not per_day:
        print("No transcripts in the last %d days (looked in %s)."
              % (args.days, T.projects_root(args.root)))
        return 0
    print("DAILY SPEND, last %d days" % args.days)
    print("  %-12s%10s%15s%15s%15s"
          % ("day", "sessions", "output", "cache write", "cache read"))
    for day in sorted(per_day):
        d = per_day[day]
        print("  %-12s%10d%15s%15s%15s"
              % (day, d["sessions"], _thousands(d["output"]),
                 _thousands(d["cache_new"]), _thousands(d["cache_read"])))
    print("")
    print("  Cache reads are roughly ten times cheaper than cache writes, which is exactly")
    print("  why a permanent addition at the TOP of the context hurts twice: it is carried")
    print("  every turn, and editing it invalidates the cache for every session after.")
    return 0


def cmd_item_cost(args):
    if args.path == "-":
        text, name = sys.stdin.read(), "stdin"
    else:
        try:
            with open(args.path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("cannot read %s: %s\n" % (args.path, exc))
            return 2
        name = os.path.basename(args.path)

    cal = load_calibration()
    tokens = estimate_tokens(text, cal)
    rows = session_starts(14, args.root)
    per_day = args.sessions_per_day or sessions_per_day(rows) or 5
    median_start = statistics.median([v for _, v, _ in rows]) if rows else 0

    if args.json:
        print(json.dumps({"item": name, "chars": len(text), "tokens_per_run": tokens,
                          "sessions_per_day": per_day,
                          "tokens_per_day": int(tokens * per_day),
                          "tokens_per_month": int(tokens * per_day * 30),
                          "share_of_session_start_pct":
                              round(100.0 * tokens / median_start, 3) if median_start else None,
                          "calibrated": is_measured(cal)}, indent=2))
        return 0

    print("RENT FOR A PERMANENT CONTEXT ADDITION: %s" % name)
    print("  characters      : %s" % _thousands(len(text)))
    print("  tokens per run  : ~%s" % _thousands(tokens))
    print("  sessions/day    : %.0f%s"
          % (per_day, "" if args.sessions_per_day else " (measured over 14 days)"))
    print("  per day         : ~%s tokens" % _thousands(tokens * per_day))
    print("  per month       : ~%s tokens" % _thousands(tokens * per_day * 30))
    if median_start:
        print("  share of a session start: %.3f%%  (median start %s)"
              % (100.0 * tokens / median_start, _thousands(median_start)))
    print("")
    print("  counter: %s" % cal.get("source"))
    if not is_measured(cal):
        print("  WARNING: the counter is NOT calibrated, this is a rough guess.")
        print("           Run `python session_tax.py calibrate` first.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="what your wiring charges per session")
    ap.add_argument("--root", default=None, help="transcript root (default ~/.claude/projects)")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("calibrate", help="measure chars-per-token on your own transcripts")
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--json", action="store_true")

    s = sub.add_parser("session-start", help="cost of a session before any work")
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--json", action="store_true")

    d = sub.add_parser("daily", help="per-day output and cache split")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--json", action="store_true")

    i = sub.add_parser("item-cost", help="rent for one permanent context addition")
    i.add_argument("path", help="file to price, or - for stdin")
    i.add_argument("--sessions-per-day", type=float, default=None)
    i.add_argument("--json", action="store_true")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    return {"calibrate": cmd_calibrate, "session-start": cmd_session_start,
            "daily": cmd_daily, "item-cost": cmd_item_cost}[args.cmd](args)


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
        # A crash must arrive as its own exit code. Sharing code 0 or 1 with a normal
        # verdict is how a dead watchdog goes on looking healthy for weeks.
        import traceback
        traceback.print_exc()
        sys.exit(4)
