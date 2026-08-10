# Our numbers

Published as a reference for the method, not as constants to copy. Every figure below came
out of the tools in this repo, run on our own fleet. Machines are anonymised; the token
counts are exactly what we got.

Run the tools on your own transcripts and your numbers will differ. That is the point: a
borrowed threshold is a noise generator (see GOTCHAS 5).

## The counter

| what | value | sample |
|---|---|---|
| Cyrillic, machine A | 2.17 chars/token | 445 pure-text replies |
| Cyrillic, machine B | 2.17 chars/token | 698 pure-text replies |
| Cyrillic, re-run on machine A, 2026-08-10 | 2.176 chars/token | 693 pure-text replies |
| the same, 7-day window | 2.174 chars/token | 379 pure-text replies |

Two machines with different workloads landed on the same rate independently, and a re-run
five days later reproduced it. That agreement is what convinced us the message-id grouping
fix was right, because the broken version had produced 1.2 and did not reproduce at all.

Note the sample sizes are lopsided. In a mostly Russian-language workflow, pure-Latin
replies are rare, so the Latin rate on the same run rested on 28 samples with a wide
spread. The tool prints `n` and the spread for exactly this reason. A rate measured on 28
samples is not the same claim as one measured on 693, and the output should not let you
forget which one you are quoting.

## The rent per session

Median cost of the **first** request of a session: the system prompt, always-loaded rules,
tool descriptions, hook output and memory index, before any work happens.

| what | value |
|---|---|
| machine A, median session start | 102,180 tokens |
| machine B, median session start | 91,549 tokens |
| machine A, 2026-07-31 | 86,748 tokens |
| machine A, 2026-08-06 | 106,405 tokens |

Two machines running the same rule set differ by more than 10,000 tokens on every single
session, which is a per-machine tax nobody had ever priced. And the trend inside one week
is up by roughly 20,000 tokens, which is not a decision anyone made. It is the sum of
many individually reasonable additions.

## What one always-loaded file costs

Measured 2026-08-10 with `session_tax.py item-cost` on our own rules file:

| what | value |
|---|---|
| file size | 79,313 characters |
| tokens per session | ~33,815 |
| share of a session start | 34.5% of a 98,002-token median |
| at 185 sessions/day | ~6.3M tokens/day, ~188M tokens/month |

One file. A third of every session start, before a single instruction is followed.

## Where the work actually went

Seven days on the hub, output tokens by kind of work:

| kind of work | share |
|---|---|
| shell commands | 54.4% |
| writing code | 15.6% |
| reading files | 12.4% |
| **pure mechanics, total** | **82%** |

Meanwhile the cheap paid coding rail sat at **4%** of its allowance, and two other paid
rails had never been measured at all.

The absolute total from that window is deliberately not quoted. It was taken with a
per-record counter, before the double-count was found, so the shares above are sound and
the total would be inflated by up to 2.6x. A share of a wrong total is still the right
share; a wrong total presented as measured is the thing this repo exists to stop.

That is the whole argument in one table. The expensive orchestrator was doing shell and
file reads, which is the least judgement-dependent work in the stack, while the buckets we
had already paid for went undrawn. Nobody decided this. It happened because the model
already holding the conversation is always the path of least resistance.

The rule we wrote afterwards: every component names, in its own docs, whose paid rail it
burns. "The orchestrator, because that is what was running" is a design defect.

## A single day, by source

`spend_audit.py --date <a real day>`:

| what | value |
|---|---|
| total | 292.4M tokens |
| live interactive work | 118.7M (41%) over 29 sessions |
| background | 173.7M (59%) |
| largest background consumer | 91.3M over **131** headless one-shot sessions |
| carried context, 24h | 863M re-read across 247 sessions |
| sessions that never called a single tool | 198 of 247, holding 35% of the re-read context |

These are the **corrected** figures. The first run of the same day reported 603.6M and
1,742M, because the aggregators were still summing usage per record rather than per
message. The ratios barely moved; the absolutes roughly halved. Left here on purpose, as
a reminder of how confident a wrong number looks.

The 131-session line is the finding no dashboard was going to hand us: something firing
per tick was opening a full model session each time. And 198 sessions carried tool
descriptions they never once used.
