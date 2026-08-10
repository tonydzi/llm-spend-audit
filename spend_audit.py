# -*- coding: utf-8 -*-
"""spend_audit.py - who actually burned your tokens yesterday, and what should not have.

Stdlib only. No LLM call, no network, no API key.

Two layers, because two different things go wrong:

  LAYER 1, by source. Split the day's tokens between your live interactive work and each
  background service, scheduled task or robot. This is where you find the task you
  disabled three weeks ago that is still firing, and the "hourly" watcher that turned out
  to be a full model session sixty times a day.

  LAYER 2, carried context. A session's cost is not its output, it is its prefix times
  its turns. A long session re-ships the same preamble on every single turn. This layer
  ranks sessions by how much context they re-read and shows which ones never called a
  single tool they were carrying descriptions for.

    python spend_audit.py                      # the day that owns tonight
    python spend_audit.py --date 2026-08-06
    python spend_audit.py --hours 48           # widen the carried-context window
    python spend_audit.py --config my.json
    python spend_audit.py --json

Prints a report, then two machine-readable lines:
    SEVERITY=GREEN|YELLOW|RED
    FLAGS=<n>

Exit codes: 0 clean, 1 findings, 2 bad invocation, 4 crash. A watchdog can branch on
those without parsing prose. Note that 1 means "found something", not "broke".
"""
import argparse
import collections
import datetime
import json
import os
import sys
import time

import transcripts as T

VERSION = "1.0.0"

DEFAULTS = {
    # Names of scheduled tasks or services that you believe are OFF. If any of them burns
    # more than `disabled_min_tokens`, that is the loudest finding this tool can produce.
    # We keep this list because a scheduler that catches up after a restart will happily
    # fire tasks that were disabled, and nothing else in the stack will tell you.
    "should_not_run": [],
    "huge_day_tokens": 400000000,      # whole-day total worth an informational flag
    "single_service_tokens": 25000000,  # one background service over this is a hog
    "background_share_warn": 0.45,      # background beating live work, when volume is real
    "background_share_min_tokens": 50000000,
    "high_frequency_sessions": 20,      # one service spawning this many sessions a day
    "disabled_min_tokens": 100000,
    # Carried-context thresholds. MEASURE YOUR OWN DISTRIBUTION BEFORE TRUSTING THESE.
    # Ours came from the 95th percentile of a real day, not from a round number we liked.
    # A threshold that fires on a third of your sessions is a threshold people mute.
    "context_hog_cache_read": 150000000,
    "long_session_turns": 700,
    "top_sessions": 6,
    "max_flagged_sessions": 3,
}


def load_config(path=None):
    cfg = dict(DEFAULTS)
    if not path:
        return cfg
    with open(path, encoding="utf-8") as fh:
        user = json.load(fh)
    unknown = set(user) - set(DEFAULTS)
    if unknown:
        # Silently ignoring a misspelled key is how a threshold you thought you raised
        # stays at its default for months.
        raise ValueError("unknown config keys: %s" % ", ".join(sorted(unknown)))
    cfg.update(user)
    return cfg


def utc_window(date):
    """Local calendar day to the UTC ISO window the transcripts are stamped in.

    Transcript timestamps are UTC. An audit day is local. Comparing them raw skews the
    day by your UTC offset, which made "today" look empty for us every night after
    local midnight until we noticed the reports were quietly wrong.
    """
    day = datetime.date.fromisoformat(date)
    local_midnight = datetime.datetime.combine(day, datetime.time()).astimezone()
    start = local_midnight.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    end = ((local_midnight + datetime.timedelta(days=1))
           .astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    return start, end


def parse_day(date, root=None):
    """{source: [tokens, sessions]}, total. Deterministic, one pass per file."""
    by_source = collections.defaultdict(lambda: [0, 0])
    total = 0
    lo, hi = utc_window(date)
    # A session that spans local midnight has tomorrow's mtime but holds today's usage.
    day = datetime.date.fromisoformat(date)
    mtimes = {date, (day + datetime.timedelta(days=1)).isoformat()}

    for ts, path in T.transcript_files(root):
        if ts.strftime("%Y-%m-%d") not in mtimes:
            continue
        tokens, user_turns, first_user, task = 0, 0, "", ""
        for rec in T.records(path):
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            stamp = rec.get("timestamp", "")
            if isinstance(usage, dict) and lo <= stamp < hi:
                tokens += (T.context_tokens(usage) + T.num(usage, "output_tokens"))
            if msg.get("role") == "user":
                text = T.user_text(msg).strip()
                if text:
                    task = task or T.scheduled_task_name(text)
                    if not text.startswith("<"):
                        user_turns += 1
                        first_user = first_user or text
        if tokens <= 0:
            continue
        total += tokens
        key = classify(task, user_turns, first_user)
        by_source[key][0] += tokens
        by_source[key][1] += 1
    return by_source, total


def classify(task, user_turns, first_user):
    """One session to one bucket. Deliberately crude: the point is live vs not-live."""
    if task:
        return "task:" + task
    if user_turns == 0:
        return "background (no human turn)"
    if user_turns <= 1:
        return "headless one-shot"
    return "LIVE (interactive)"


def scan_sessions(hours=24, root=None):
    """Per-session carried-context cost over the last N hours."""
    cutoff = time.time() - hours * 3600
    out = []
    for _, path in T.transcript_files(root):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        prefix = None
        turns = cache_read = total = 0
        tools, task = set(), ""
        for rec in T.records(path):
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if isinstance(usage, dict):
                turns += 1
                shipped = T.context_tokens(usage)
                if prefix is None:
                    prefix = shipped
                cache_read += T.num(usage, "cache_read_input_tokens")
                total += shipped + T.num(usage, "output_tokens")
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    name = str(block.get("name", ""))
                    if block.get("type") == "tool_use" and name.startswith("mcp__"):
                        parts = name.split("__")
                        if len(parts) > 1:
                            tools.add(parts[1])
            if not task and msg.get("role") == "user":
                task = T.scheduled_task_name(T.user_text(msg))
        if turns:
            out.append({"prefix": prefix or 0, "turns": turns, "cache_read": cache_read,
                        "total": total, "servers": sorted(tools), "task": task,
                        "file": os.path.basename(path)[:8]})
    return out


def fmt(n):
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    return "{:,}".format(int(n)).replace(",", " ")


def source_flags(by_source, total, cfg):
    flags = []
    should_not_run = set(cfg["should_not_run"])
    for key, (tokens, sessions) in by_source.items():
        name = key.split("task:")[-1]
        if name in should_not_run and tokens >= cfg["disabled_min_tokens"]:
            flags.append("CRITICAL: '%s' is supposed to be OFF and burned %s (%d runs)"
                         % (name, fmt(tokens), sessions))
    for key, (tokens, sessions) in by_source.items():
        if key.startswith("LIVE"):
            continue
        if sessions >= cfg["high_frequency_sessions"]:
            flags.append("CRITICAL: '%s' started %d sessions in one day and burned %s. "
                         "A model session per tick is almost never the right design"
                         % (key, sessions, fmt(tokens)))
    for key, (tokens, sessions) in by_source.items():
        if key.startswith("task:") and sessions >= 2:
            flags.append("'%s' fired %d times in one day (%s). A daily task firing twice "
                         "means the scheduler is catching up or double-firing"
                         % (key, sessions, fmt(tokens)))
    background = [(k, v) for k, v in by_source.items() if not k.startswith("LIVE")]
    background.sort(key=lambda kv: kv[1][0], reverse=True)
    if background and background[0][1][0] >= cfg["single_service_tokens"]:
        key, (tokens, sessions) = background[0]
        flags.append("'%s' alone ate %s over %d runs, the largest background consumer"
                     % (key, fmt(tokens), sessions))
    bg_total = sum(v[0] for _, v in background)
    if (bg_total > cfg["background_share_min_tokens"] and total
            and bg_total / total > cfg["background_share_warn"]):
        flags.append("background is %.0f%% of all tokens (%s), more than your live work"
                     % (bg_total / total * 100, fmt(bg_total)))
    if total > cfg["huge_day_tokens"]:
        flags.append("very large day: %s tokens in total" % fmt(total))
    return flags, bg_total


def context_section(hours, cfg, root=None):
    rows = scan_sessions(hours, root)
    lines, flags = [], []
    if not rows:
        return ["", "CARRIED CONTEXT: no sessions in the window."], flags

    rows.sort(key=lambda r: r["cache_read"], reverse=True)
    carried = sum(r["cache_read"] for r in rows)
    no_tools = [r for r in rows if not r["servers"]]

    lines.append("")
    lines.append("CARRIED CONTEXT over %dh: %d sessions, %s re-read"
                 % (hours, len(rows), fmt(carried)))
    if carried:
        lines.append("  sessions that never called a single MCP tool: %d (%s, %.0f%% of it)"
                     % (len(no_tools), fmt(sum(r["cache_read"] for r in no_tools)),
                        sum(r["cache_read"] for r in no_tools) / carried * 100))
    lines.append("")
    lines.append("  top sessions by re-read context:")
    lines.append("  %11s %6s %8s  %s" % ("cache read", "turns", "prefix", "tools / task"))
    for r in rows[:cfg["top_sessions"]]:
        who = r["task"] or (",".join(r["servers"]) if r["servers"] else "no tool ever called")
        lines.append("  %11s %6d %8d  %s" % (fmt(r["cache_read"]), r["turns"], r["prefix"], who[:52]))

    hogs = [r for r in rows
            if r["cache_read"] >= cfg["context_hog_cache_read"]
            or r["turns"] >= cfg["long_session_turns"]]
    # One flag per session, and only for the worst few. Twelve near-identical alerts about
    # one fact is how an alert channel stops being read at all.
    for r in hogs[:cfg["max_flagged_sessions"]]:
        who = r["task"] or r["file"]
        flags.append("session '%s': %s of re-read context over %d turns at prefix %d. "
                     "Split the session or compact it more often"
                     % (who, fmt(r["cache_read"]), r["turns"], r["prefix"]))
    if len(hogs) > cfg["max_flagged_sessions"]:
        flags.append("%d more sessions above the same thresholds, see the table"
                     % (len(hogs) - cfg["max_flagged_sessions"]))
    return lines, flags


def build_report(date, hours, cfg, root=None):
    by_source, total = parse_day(date, root)
    ctx_lines, ctx_flags = context_section(hours, cfg, root)

    if total == 0:
        flags = ctx_flags
        lines = ["SPEND AUDIT %s: no session spend recorded for this day." % date] + ctx_lines
    else:
        live = sum(v[0] for k, v in by_source.items() if k.startswith("LIVE"))
        flags, background = source_flags(by_source, total, cfg)
        flags += ctx_flags
        rows = sorted(by_source.items(), key=lambda kv: kv[1][0], reverse=True)
        lines = ["SPEND AUDIT %s" % date,
                 "total %s | live %s (%.0f%%) | background %s (%.0f%%)"
                 % (fmt(total), fmt(live), live / total * 100,
                    fmt(background), background / total * 100),
                 "",
                 "top consumers:"]
        for key, (tokens, sessions) in rows[:8]:
            lines.append("  %7s (%4.1f%%) x%-3d %s"
                         % (fmt(tokens), tokens / total * 100, sessions, key))
        lines += ctx_lines

    severity = "GREEN"
    if any(f.startswith("CRITICAL") for f in flags):
        severity = "RED"
    elif flags:
        severity = "YELLOW"

    if flags:
        lines += ["", "FINDINGS:"] + ["  - " + f for f in flags]
    else:
        lines += ["", "No anomalies. Background services are behaving."]
    return "\n".join(lines), severity, flags, by_source, total


def main(argv=None):
    ap = argparse.ArgumentParser(description="who burned your tokens, and what should not have")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, default = the day that owns tonight")
    ap.add_argument("--hours", type=int, default=24, help="carried-context window")
    ap.add_argument("--config", default=None, help="JSON file overriding thresholds")
    ap.add_argument("--root", default=None, help="transcript root (default ~/.claude/projects)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        sys.stderr.write("config problem: %s\n" % exc)
        return 2

    date = args.date
    if date is None:
        # The nightly report is about "the day that owns tonight". Before 06:00 local you
        # are still inside yesterday's night window, so yesterday is the honest answer.
        date = (datetime.datetime.now() - datetime.timedelta(hours=6)).date().isoformat()
    else:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            sys.stderr.write("bad --date %r, expected YYYY-MM-DD\n" % date)
            return 2

    report, severity, flags, by_source, total = build_report(date, args.hours, cfg, args.root)
    if args.json:
        print(json.dumps({"date": date, "severity": severity, "flags": flags,
                          "total_tokens": total,
                          "by_source": {k: {"tokens": v[0], "sessions": v[1]}
                                        for k, v in by_source.items()}},
                         ensure_ascii=False, indent=2))
    else:
        print(report)
        print("SEVERITY=%s" % severity)
        print("FLAGS=%d" % len(flags))
    return 1 if flags else 0


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
