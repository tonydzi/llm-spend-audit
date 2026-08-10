# Method

Three ideas. The tools are small; these are the part worth stealing.

## 1. Two axes, and almost everyone measures only the first

**Axis A, the price of the work.** Which model runs which job. This is the axis every
"cut your LLM costs" post is about, and it is well covered elsewhere.

**Axis B, the standing rent your own wiring charges.** The rules file loaded into every
session. The startup hook that prints eleven lines. The tool descriptions. The memory
index. The MCP server registered on a machine where three of its forty tools are ever
called. You pay for these in every session, forever, and they are visible for exactly one
moment: while you are building them.

Axis B has a property that makes it dangerous. Nobody ever decides to spend it. It
accumulates one helpful addition at a time, each of which is obviously worth it on its
own. Six weeks later the preamble has doubled and no single commit is to blame.

`session_tax.py item-cost` prices one addition before you commit to it. Run it on the
hook output you are about to add. The number that changes minds is not tokens per run, it
is tokens per month, because that is what you are actually signing up for.

## 2. An unused allowance is a loss, not a saving

The invoice is identical whether you drew 5% of the bucket or 95%. So "we were careful
with the expensive vendor" is only a real saving if the work moved somewhere cheaper. If
it moved nowhere, you paid full price for an empty bucket **and** did the work on the
priciest rail you own.

This flips the default question. Not "how do we spend less" but "which paid bucket has
the most room left, and why is that work not going there".

The rule this produced for us: every component we build carries a line in its docs naming
**whose paid rail it burns**. If that line says the orchestrator model because that is
what happens to be running, that is a design defect, not an answer. It is the same
mistake as writing all your batch jobs in the language your IDE opened with.

## 3. Origin tags, never averages

Bucket percentages usually live in a vendor's web panel and no script can read them. So
numbers of different origin are kept apart and never blended:

| origin | where it came from | proof |
|---|---|---|
| `measured` | counted from your own logs | the log file |
| `declared` | a human read a panel and typed it | a date and a name |
| `vendor` | the vendor's own tooling reported it | its transcript |

Three rules fall out of this:

- **"not measured" is not zero.** A rail nobody has looked at prints as unmeasured and
  gets escalated as a blind spot. Silence has to be proven, not accepted. We nearly let a
  plan run unwatched because "no reading" and "no usage" looked the same in a table.
- **On the same date, the vendor's own figure beats a human's.** We once held a hand-typed
  30% and a vendor-reported 3.0% for the same day. A plan decision was about to be made on
  the wrong one.
- **A large gap gets printed, not resolved.** A 10x difference between a vendor figure and
  a human figure is usually two different buckets (a coding allowance versus the whole
  plan), not a dispute about the truth. Averaging them would invent a number that describes
  nothing.

## 4. Measure the ruler before you cut

Every threshold in `spend_audit.example.json` came from a percentile of a real day, not
from a round number that sounded serious. Our first carried-context threshold fired on 49
of 147 sessions. A gate that goes off a third of the time is one people mute inside a
week, and after that it protects nothing. Take your own distribution first, then set the
line near the outliers.

The same applies to the counter itself. `calibrate` refuses to call itself measured until
it has eight clean samples, and says so in every printout that uses it.
