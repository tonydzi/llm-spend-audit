# -*- coding: utf-8 -*-
"""Reading Claude Code transcripts, safely. Stdlib only, no network, no LLM.

Every tool in this kit reads the same thing: the JSONL transcripts the harness writes
under ~/.claude/projects/<project-slug>/*.jsonl. Those files contain the REAL usage
numbers the vendor billed, not an estimate, which is why this kit needs no API key.

One module instead of three copies of the same parser, because the two traps below cost
us real money and we would rather fix them in one place.

TRAP 1 - the numbers in a file you did not write are not numbers until you check them.
A single record with usage={"input_tokens": "x"} raised TypeError, an outer except
swallowed it, and the WHOLE session was dropped from the report. Silent data loss that
looks exactly like a quiet day. Hence num().

TRAP 2 - output_tokens is charged per MESSAGE and repeated in every record of that
message. See session_tax.calibrate for what that does to a naive calibration.

Environment:
    CLAUDE_PROJECTS_DIR   override the transcript root (used by the tests)
"""
import datetime
import glob
import json
import os

DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def projects_root(explicit=None):
    """Where the transcripts live. Explicit argument wins, then env, then the default."""
    return explicit or os.environ.get("CLAUDE_PROJECTS_DIR") or DEFAULT_ROOT


def transcript_files(root=None, days=None, min_bytes=0):
    """[(mtime, path)] sorted oldest first. `days` filters by file mtime.

    A missing or empty root is not an error here: "no transcripts" is a legitimate
    answer that every caller must be able to print without crashing.
    """
    root = projects_root(root)
    out = []
    if not os.path.isdir(root):
        return out
    now = datetime.datetime.now()
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            if min_bytes and os.path.getsize(path) < min_bytes:
                continue
            ts = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            continue
        if days is not None and (now - ts).days > days:
            continue
        out.append((ts, path))
    out.sort()
    return out


def records(path, needle=None):
    """Yield parsed JSON records. A broken line is skipped, never fatal.

    `needle` is a cheap substring pre-filter (e.g. '"usage"') so we do not JSON-parse
    every line of a 200 MB transcript when we only care about a fraction of them.
    """
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            if needle and needle not in line:
                continue
            line = line.lstrip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def num(usage, key):
    """usage[key] as a number, or 0.

    Booleans are not numbers here: `True` is an `int` in Python, so a sloppy check counts
    it as one token.

    A numeric STRING is coerced rather than dropped. Refusing "123" is the same class of
    silent data loss as crashing on "x", only in the other direction: it quietly reports
    spend that really happened as zero. An external reviewer caught this one.
    """
    if not isinstance(usage, dict):
        return 0
    v = usage.get(key, 0)
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0
    return 0


def context_tokens(usage):
    """Everything that had to be shipped INTO the model for this turn.

    input + cache_creation + cache_read. This is the number that grows when your
    always-loaded files grow, and it is the one this kit calls rent.
    """
    return (num(usage, "input_tokens")
            + num(usage, "cache_creation_input_tokens")
            + num(usage, "cache_read_input_tokens"))


def assistant_messages(path):
    """{message_id: {"tokens": int, "text": str, "kinds": set}} for one transcript.

    Grouped by message.id ON PURPOSE - see TRAP 2 in the module docstring. `kinds` is
    the set of content-block types in the message, so a caller can keep only the pure
    text ones ({"text"}) when it needs text-to-token pairs it can trust.
    """
    msgs = {}
    for rec in records(path, needle='"assistant"'):
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        mid = msg.get("id")
        if not mid:
            continue
        entry = msgs.setdefault(mid, {"tokens": 0, "text": [], "kinds": set()})
        # The value is meant to be identical in every record of the message. Take the
        # largest rather than the last: if a partial record ever carries a lower interim
        # count, "last wins" would under-report the message and silently inflate the
        # measured chars-per-token rate.
        entry["tokens"] = max(entry["tokens"], num(msg.get("usage"), "output_tokens"))
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            entry["kinds"].add(block.get("type"))
            if block.get("type") == "text":
                entry["text"].append(block.get("text") or "")
    for entry in msgs.values():
        entry["text"] = "".join(entry["text"])
    return msgs


def merge_usage(into, usage):
    """Merge one record's usage into a message's totals, field by field, taking the max.

    The usage object is REPEATED in every record of a message, so merging with max keeps
    the message's real cost while a naive sum multiplies it. Measured on a real fleet:
    1672 usage records covered 746 distinct messages, and summing per record inflated
    output tokens by 2.58x. Every aggregate in this kit goes through here.
    """
    for key in ("output_tokens", "input_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = num(usage, key)
        if value > into.get(key, 0):
            into[key] = value
    return into


def scan_session(path):
    """One pass over a transcript, returning everything both tools need.

    {"messages": [{"usage": merged, "ts": first timestamp}, ...] in order,
     "servers": sorted MCP servers actually called,
     "task": scheduled-task name or "",
     "user_turns": count of real human turns,
     "first_user": first human message}

    `messages` holds ONE entry per assistant message, not per record. That distinction is
    the whole point of this file: see merge_usage above.
    """
    order, by_id = [], {}
    servers, task, user_turns, first_user = set(), "", 0, ""
    anonymous = 0

    for rec in records(path):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if isinstance(usage, dict):
            mid = msg.get("id")
            if not mid:
                anonymous += 1
                mid = "_anon_%d" % anonymous      # no id means it cannot be a duplicate
            entry = by_id.get(mid)
            if entry is None:
                # An explicit JSON null makes .get(key, "") return None, not "", and a
                # later `lo <= ts < hi` then raises TypeError against a string bound.
                # A whole day's audit died on one null timestamp.
                stamp = rec.get("timestamp") or ""
                entry = {"usage": {}, "ts": stamp if isinstance(stamp, str) else ""}
                by_id[mid] = entry
                order.append(entry)
            merge_usage(entry["usage"], usage)
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                name = str(block.get("name", ""))
                if block.get("type") == "tool_use" and name.startswith("mcp__"):
                    parts = name.split("__")
                    if len(parts) > 1:
                        servers.add(parts[1])
        if msg.get("role") == "user":
            text = user_text(msg).strip()
            if text:
                task = task or scheduled_task_name(text)
                if not text.startswith("<"):
                    user_turns += 1
                    first_user = first_user or text

    return {"messages": order, "servers": sorted(servers), "task": task,
            "user_turns": user_turns, "first_user": first_user}


def scheduled_task_name(text):
    """The harness marks an automated run with <scheduled-task name="...">. Returns "" if absent."""
    if not isinstance(text, str) or "<scheduled-task name=" not in text:
        return ""
    head = text.split('<scheduled-task name="', 1)[1]
    return head.split('"', 1)[0] if '"' in head else ""


def user_text(message):
    """First-user-message text as a plain string, whatever shape content came in."""
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""
