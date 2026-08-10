# Gotchas

Every one of these cost us something real. They are listed roughly in order of how badly
each one can mislead you.

## 1. `output_tokens` is per MESSAGE, and it is repeated in every record of that message

This is the one to remember even if you never run this kit.

In a transcript, one assistant message can appear across several JSONL records. The
`usage` object is repeated in each of them, and its `output_tokens` covers the **whole
message**: thinking blocks and tool calls included, not just the text you can see.

Naively dividing visible characters by that number gave us **1.2 characters per token for
Cyrillic**, which is not physically possible for that script. We nearly shipped a context
budget built on it.

The fix is two lines, and it is the most useful thing in this repo:

```python
# group records by message.id, then keep only messages that are pure text
if entry["kinds"] - {"text"}:
    continue
```

Tested three ways in `tests/test_kit.py::TestCalibration`: the true rate on clean data,
poisoned messages excluded, and duplicate records not summed. Remove the fix and the third
one goes red immediately.

**And it bites a second time, further downstream.** We fixed the calibration, shipped, and
an external review panel then found the same trap alive in both aggregators: the day
report and the daily table were summing `usage` per record. Measured on real transcripts,
1672 usage records covered 746 distinct messages, and output tokens came out **2.58x**
too high. A carried-context table also counted a 324-turn session as 800-odd turns and
therefore ranked the wrong sessions as expensive.

Every aggregate now goes through `transcripts.merge_usage()` and `transcripts.scan_session()`,
which return one entry per message. If you take a number out of a transcript, ask yourself
whether your loop is over records or over messages. It is almost always meant to be
messages.

## 2. Numbers in a file you did not write are not numbers

One record carried `usage={"input_tokens": "x"}`. That raised `TypeError`, an outer
`except` swallowed it, and the **entire session** dropped out of the report. Silent data
loss that looks exactly like a quiet day.

Every numeric read goes through `transcripts.num()`. It returns 0 for `None`, for
booleans and for unparseable strings, and it **coerces** a numeric string like `"123"`
rather than dropping it. Both directions are silent data loss: crashing on `"x"` loses a
session, and quietly zeroing `"123"` reports spend that really happened as nothing. The
second one was caught by an external reviewer, not by us.

Booleans matter separately: `True` is an `int` in Python, so a sloppy check counts it as
one token.

## 3. Transcript timestamps are UTC, your audit day is local

Comparing them raw skews the day by your UTC offset. Ours made "today" look empty every
night after local midnight, and the nightly report was quietly wrong for a while before
anyone noticed. See `spend_audit.utc_window()`.

Related: a session that runs across local midnight has tomorrow's file mtime while holding
today's usage, so the day scan looks at two mtimes, not one.

## 4. A watchdog must not share an exit code with a verdict

`spend_audit.py` returns 1 for "found something" and 4 for "crashed". If a crash returns 1,
a dead tool is indistinguishable from a healthy one that had nothing to report, and it can
stay dead for weeks while looking amber. Every entry point here carries a crash guard:

```python
except BaseException:
    traceback.print_exc()
    sys.exit(4)
```

with `except SystemExit: raise` above it, because `sys.exit` is itself a `BaseException`
and without that line a clean exit 0 gets repainted as a crash. We shipped that bug and
caught it on the first run.

## 5. Thresholds copied from someone else are noise generators

Our first carried-context threshold fired on 49 of 147 sessions. Daily red is the same as
no alarm. Measure your own distribution, put the line near the outliers, and re-measure
once a quarter. The defaults in `spend_audit.example.json` are ours, they are labelled as
such, and they were derived **before** the per-message fix in gotcha 1, so on corrected
numbers they sit higher than they should. Take your own.

Same reason a session gets **one** flag, not one per rule it trips, and only the worst
three are reported. Twelve near-identical alerts about one fact is how an alert channel
stops being read.

## 6. `age or 999` breaks on the day that matters

```python
fresh = [r for r in rows if (_age_days(r["date"]) or 999) <= 7]   # WRONG
```

An age of 0, meaning a reading taken today, is falsy. So today's numbers were the exact
pair this check could never see, which is the pair a same-day conflict lives in. Found by
a test, never by reading the line. Use an explicit `is not None`.

## 7. Counting by vendor name while the log writes the engine name

Our call log recorded the CLI's name and the report looked up the vendor's name. A rail we
used every day printed as zero calls. Undercounting a rail argues for cancelling a plan you
are actively using, so `rails.json` takes a list of `engines` per vendor.

The mirror image is just as bad: we also had two copies of the same tool writing to two
different logs, and the counter read only one of them. Read every log, then dedup by
`(ts, engine, task)`. Overstating utilisation is exactly as forbidden as understating it.

## 8. Picking the wrong bucket reports underuse at the ceiling

A vendor may report several allowances at once, for example a short rolling window and a
weekly one. Taking the first showed 3% while the binding weekly bucket sat at 90%. Take the
**fullest** bucket: that is the one that will stop you.

And if the freshest transcript has no rate-limit block at all, that does not mean the
bucket is empty. It means the CLI was not used there. Walk back through older files.

## 9. A stale figure with today's date on it

When a vendor record's timestamp was unreadable, an early version substituted today's date.
That turned a week-old number into a fresh one, and money decisions are made on these. Now
it refuses to record anything rather than guess the date.

## 10. Files smaller than 5 KB are skipped

`session_tax` ignores tiny transcripts, because a session that produced three lines has a
"median start" that is really just noise. If you have a workflow of genuinely tiny
sessions, lower `min_bytes` in `transcripts.transcript_files()`. It is a deliberate choice,
not an accident, but it is invisible until you know about it.

Similarly, the growth trend ignores days with fewer than three sessions. A day holding one
session has a median that is just that session, and anchoring a growth claim to it turns
noise into a headline.

## 11. Cache reads are cheap, cache writes are not

Roughly a tenfold difference. This is why a permanent addition at the **top** of the
context hurts twice: it is carried on every turn, and editing it invalidates the cache for
every session afterwards. It is also why a long session is not automatically wasteful and a
short one is not automatically thrifty. Rank by re-read context, which is prefix times
turns, not by output.
